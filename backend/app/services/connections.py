import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as time_
from decimal import Decimal

from sqlalchemy import MetaData, Table, create_engine, distinct, func, inspect, select, text
from sqlalchemy import types as sa_types
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.models.data_source import DataSourceType

CONNECT_TIMEOUT = 5
SAMPLE_TIMEOUT = 5  # seconds, query-side — Module 10 data preview
PROFILE_TIMEOUT = 8  # seconds — Module 12 profiling touches the whole table (counts/distincts)
LOW_CARDINALITY_THRESHOLD = 20  # below this distinct count, a sample of values is worth sending to Mistral
SAMPLE_VALUES_LIMIT = 10
# Module 14 extension "primitives gold" §3.1 — a gold filter's literal value must be checked
# against the column's REAL distinct values, never a new raw-row scan at filter-validation
# time ("hygiène données" §2). A dedicated, higher cap than SAMPLE_VALUES_LIMIT: below this
# distinct count, the LIMIT below is guaranteed to capture EVERY distinct value that exists
# (not a truncated sample), which is exactly what a strict enum-membership check needs — the
# existing sample_values (LIMIT 10) can silently truncate a 15-distinct column, which would be
# unsafe to use for rejecting a value.
FILTER_ENUM_MAX = 50


class ConnectionError_(Exception):
    pass


@dataclass
class TestResult:
    reachable: bool
    message: str
    latency_ms: int | None = None
    version: str | None = None


@dataclass
class SchemaInfo:
    name: str
    tables: list[str] = field(default_factory=list)


@dataclass
class IntrospectResult:
    schemas: list[SchemaInfo] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)


# Extensions ingest_bronze.py.j2's _ingest_from_object_store() actually knows how to read —
# no point surfacing files as pickable bronze candidates that would just get skipped.
_INGESTIBLE_EXTENSIONS = ("csv", "parquet", "json", "ndjson")

# Oracle's own catalog/system schemas — never business data, but SQLAlchemy's
# get_schema_names() returns them alongside real ones with no built-in way to tell them
# apart. Unlike Postgres' well-known information_schema/pg_catalog, Oracle has no single
# marker for "system schema", so this is a name allowlist-by-exclusion built from the
# schemas every stock Oracle install ships with.
_ORACLE_SYSTEM_SCHEMAS = {
    "sys", "system", "mdsys", "ctxsys", "xdb", "outln", "orddata", "ordsys", "ordplugins",
    "wmsys", "dbsnmp", "appqossys", "olapsys", "dvsys", "dvf", "lbacsys",
    "gsmadmin_internal", "dip", "audsys", "ojvmsys", "remote_scheduler_agent", "sys$umf",
}

# Oracle Text auto-generates a "DR$<index_name>$<letter>" family of auxiliary storage tables
# for every CONTEXT/CTXCAT index — never business data, but they sit as ordinary tables in
# the owning schema (unlike system schemas, filtering by schema name can't catch these).
_ORACLE_TEXT_INDEX_TABLE = re.compile(r"^dr\$.+\$.$", re.IGNORECASE)


def _build_dsn(type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str) -> str:
    if type_ == DataSourceType.postgresql:
        return f"postgresql+psycopg://{username}:{password}@{host}:{port}/{database_name or 'postgres'}"
    if type_ == DataSourceType.mysql:
        return f"mysql+pymysql://{username}:{password}@{host}:{port}/{database_name or ''}"
    if type_ == DataSourceType.oracle:
        return f"oracle+oracledb://{username}:{password}@{host}:{port}/?service_name={database_name}"
    raise ConnectionError_(f"Type de source non supporté pour SQLAlchemy : {type_}")


def _sql_engine(type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str) -> Engine:
    dsn = _build_dsn(type_, host, port, database_name, username, password)
    connect_args = {}
    if type_ == DataSourceType.postgresql:
        connect_args = {"connect_timeout": CONNECT_TIMEOUT}
    elif type_ == DataSourceType.mysql:
        connect_args = {"connect_timeout": CONNECT_TIMEOUT}
    elif type_ == DataSourceType.oracle:
        connect_args = {"tcp_connect_timeout": CONNECT_TIMEOUT}
    return create_engine(dsn, connect_args=connect_args, pool_pre_ping=False)


def _minio_client(host: str, port: int, username: str, password: str, options: dict):
    from minio import Minio

    secure = bool((options or {}).get("secure", False))
    endpoint = host if ":" in host else f"{host}:{port}"
    return Minio(endpoint, access_key=username, secret_key=password, secure=secure, region=(options or {}).get("region") or None)


def test_connection(type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str, options: dict | None = None) -> TestResult:
    start = time.monotonic()
    try:
        if type_ == DataSourceType.minio:
            client = _minio_client(host, port, username, password, options or {})
            # A fixed target bucket lets the IAM policy stay scoped to that one bucket
            # (s3:ListBucket on its ARN) instead of requiring the account-wide
            # s3:ListAllMyBuckets that a full client.list_buckets() call needs.
            bucket = (options or {}).get("bucket")
            if bucket:
                if not client.bucket_exists(bucket):
                    return TestResult(reachable=False, message=f"Connexion établie, mais le bucket '{bucket}' est introuvable ou inaccessible avec ces identifiants.")
            else:
                client.list_buckets()
            latency_ms = int((time.monotonic() - start) * 1000)
            return TestResult(reachable=True, message="Connexion réussie.", latency_ms=latency_ms)

        engine = _sql_engine(type_, host, port, database_name, username, password)
        with engine.connect() as conn:
            # Oracle has no table-less SELECT — it requires the DUAL pseudo-table.
            conn.execute(text("SELECT 1 FROM DUAL" if type_ == DataSourceType.oracle else "SELECT 1"))
            version = None
            try:
                if type_ == DataSourceType.postgresql:
                    version = conn.execute(text("SHOW server_version")).scalar()
                elif type_ == DataSourceType.mysql:
                    version = conn.execute(text("SELECT VERSION()")).scalar()
                elif type_ == DataSourceType.oracle:
                    version = conn.execute(text("SELECT banner FROM v$version WHERE ROWNUM = 1")).scalar()
            except Exception:
                version = None
        engine.dispose()
        latency_ms = int((time.monotonic() - start) * 1000)
        return TestResult(reachable=True, message="Connexion réussie.", latency_ms=latency_ms, version=str(version) if version else None)
    except Exception as exc:
        return TestResult(reachable=False, message=clean_error(exc))


def introspect(type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str, options: dict | None = None, bucket_override: str | None = None) -> IntrospectResult:
    if type_ == DataSourceType.minio:
        # Either the connection is pre-scoped to one bucket (options.bucket, for
        # least-privilege IAM), or the caller just picked a specific bucket to browse
        # into (bucket_override, from an unscoped multi-bucket connection) — same
        # object-listing behavior either way.
        bucket = bucket_override or (options or {}).get("bucket")
        if bucket:
            client = _minio_client(host, port, username, password, options or {})
            objects = sorted(
                o.object_name for o in client.list_objects(bucket, recursive=True)
                if not o.is_dir and o.object_name.rsplit(".", 1)[-1].lower() in _INGESTIBLE_EXTENSIONS
            )
            return IntrospectResult(buckets=[bucket], objects=objects)
        client = _minio_client(host, port, username, password, options or {})
        buckets = [b.name for b in client.list_buckets()]
        return IntrospectResult(buckets=buckets)

    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        inspector = inspect(engine)
        schema_names = inspector.get_schema_names()
        schemas = []
        for schema_name in schema_names:
            if schema_name in ("information_schema", "pg_catalog", "pg_toast") or schema_name.lower() in _ORACLE_SYSTEM_SCHEMAS:
                continue
            try:
                tables = [t for t in inspector.get_table_names(schema=schema_name) if not _ORACLE_TEXT_INDEX_TABLE.match(t)]
            except Exception:
                tables = []
            schemas.append(SchemaInfo(name=schema_name, tables=tables))
        return IntrospectResult(schemas=schemas)
    finally:
        engine.dispose()


def clean_error(exc: Exception) -> str:
    message = str(exc).split("\n")[0]
    if len(message) > 300:
        message = message[:300] + "…"
    return f"Connexion impossible : {message}"


def list_columns(type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str, schema: str, table: str) -> list[dict]:
    """Column names/types only, via schema reflection — no query executed against the table's
    rows. Cheap enough to call per-table from an annotation UI (Module 13), unlike
    `profile_table` which aggregates over every row."""
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        metadata = MetaData()
        table_obj = Table(table, metadata, schema=schema, autoload_with=engine)
        return [{"name": c.name, "type": str(c.type)} for c in table_obj.columns]
    finally:
        engine.dispose()


def get_table_constraints(type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str, schema: str, table: str) -> dict:
    """Module 14 correctif §3.2/3.3 — real PK/UNIQUE/FK constraints via schema reflection only
    (no query against the table's rows). A hard-proof shortcut for relationship_detect.py's
    "parent PK-like" and "name affinity" gates: an introspected constraint outranks a sampled
    ratio or a name heuristic. Best-effort per constraint kind — a source/driver that can't
    reflect one kind (e.g. no privilege on the constraint catalog) degrades that kind to empty
    rather than failing the whole call, so the gates just fall back to their stats-based path."""
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        inspector = inspect(engine)
        try:
            pk = inspector.get_pk_constraint(table, schema=schema)
            pk_columns = set(pk.get("constrained_columns") or [])
        except Exception:
            pk_columns = set()
        try:
            uniques = inspector.get_unique_constraints(table, schema=schema)
            unique_columns = {c for u in uniques for c in (u.get("column_names") or [])}
        except Exception:
            unique_columns = set()
        try:
            fks_raw = inspector.get_foreign_keys(table, schema=schema)
            foreign_keys = [
                {
                    "columns": tuple(fk.get("constrained_columns") or []),
                    "referred_table": fk.get("referred_table") or "",
                    "referred_columns": tuple(fk.get("referred_columns") or []),
                }
                for fk in fks_raw
            ]
        except Exception:
            foreign_keys = []
        return {"pk_columns": pk_columns, "unique_columns": unique_columns, "foreign_keys": foreign_keys}
    except Exception:
        return {"pk_columns": set(), "unique_columns": set(), "foreign_keys": []}
    finally:
        engine.dispose()


def table_exists(type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str, schema: str, table: str) -> bool:
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        return inspect(engine).has_table(table, schema=schema)
    finally:
        engine.dispose()


class SampleTimeout(ConnectionError_):
    pass


@dataclass
class SampleResult:
    columns: list[dict]
    rows: list[dict]


def _looks_like_timeout(exc: Exception) -> bool:
    text_ = str(exc).lower()
    return "timeout" in text_ or "interrupted" in text_ or "canceling statement" in text_


def sample_rows(
    type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str,
    schema: str, table: str, limit: int, offset: int,
) -> SampleResult:
    """Bounded, read-only sample of a real table (Module 10). Identifiers are reflected and
    quoted by SQLAlchemy — never string-interpolated — via `Table(..., autoload_with=engine)`."""
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        metadata = MetaData()
        table_obj = Table(table, metadata, schema=schema, autoload_with=engine)
        stmt = select(table_obj).limit(limit).offset(offset)
        try:
            with engine.begin() as conn:
                if type_ == DataSourceType.postgresql:
                    # Postgres' SET does not accept bound parameters over the extended
                    # protocol — safe to inline since this is a fixed internal constant.
                    conn.execute(text(f"SET LOCAL statement_timeout = {SAMPLE_TIMEOUT * 1000}"))
                elif type_ == DataSourceType.mysql:
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={SAMPLE_TIMEOUT * 1000}"))
                result = conn.execute(stmt)
                columns = [{"name": c.name, "type": str(c.type)} for c in table_obj.columns]
                rows = [dict(r) for r in result.mappings().all()]
        except OperationalError as exc:
            if _looks_like_timeout(exc):
                raise SampleTimeout("Délai dépassé lors de la lecture de l'échantillon.") from exc
            raise
        return SampleResult(columns=columns, rows=rows)
    finally:
        engine.dispose()


def measure_overlap(
    type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str,
    child_schema: str, child_table: str, child_column: str,
    parent_schema: str, parent_table: str, parent_column: str,
    sample_size: int, timeout: int,
) -> dict:
    """Module 14 §4.2 — bounded, read-only estimate of FK-likeness: what fraction of a
    *sampled* set of the child column's distinct values also appear among the parent column's
    distinct values. Never a full scan on the child side — LIMIT bounds the distinct-value
    sample, so cost stays flat regardless of how large the child (typically fact) table is.
    The parent side's distinct values are read in full, which is fine since it's expected to
    be the reference/dimension side of the relationship (small). Returns
    {"sampled", "matched", "distinct_parent"} — the caller derives match_rate/confidence."""
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        child_tbl = Table(child_table, MetaData(), schema=child_schema, autoload_with=engine)
        parent_tbl = Table(parent_table, MetaData(), schema=parent_schema, autoload_with=engine)
        child_col = child_tbl.c[child_column]
        parent_col = parent_tbl.c[parent_column]

        sample_subq = select(child_col.distinct().label("v")).where(child_col.isnot(None)).limit(sample_size).subquery()
        parent_distinct_subq = select(parent_col.distinct().label("v")).where(parent_col.isnot(None)).subquery()

        try:
            with engine.begin() as conn:
                if type_ == DataSourceType.postgresql:
                    conn.execute(text(f"SET LOCAL statement_timeout = {timeout * 1000}"))
                elif type_ == DataSourceType.mysql:
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={timeout * 1000}"))
                sampled = conn.execute(select(func.count()).select_from(sample_subq)).scalar() or 0
                if sampled == 0:
                    return {"sampled": 0, "matched": 0, "distinct_parent": 0}
                matched = conn.execute(
                    select(func.count()).select_from(sample_subq).where(sample_subq.c.v.in_(select(parent_distinct_subq.c.v)))
                ).scalar() or 0
                distinct_parent = conn.execute(select(func.count()).select_from(parent_distinct_subq)).scalar() or 0
        except OperationalError as exc:
            if _looks_like_timeout(exc):
                raise SampleTimeout("Délai dépassé lors du calcul de recouvrement.") from exc
            raise
        return {"sampled": int(sampled), "matched": int(matched), "distinct_parent": int(distinct_parent)}
    finally:
        engine.dispose()


def row_count(
    type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str,
    schema: str, table: str, timeout: int,
) -> int | None:
    """Module 14 §5.2 — a single bounded COUNT(*), the deterministic `from_profile.row_count`
    evidence anchor. Kept as its own small query rather than folded into profile_table()'s
    combined aggregate (which already computes a row total internally, just never returns it)
    to avoid touching profile_table()'s contract — gold_profile.py (Module 12) depends on it
    unchanged. None on timeout, not an exception: the caller treats a missing row_count as "no
    deterministic anchor" (§5.2's anti-loop rule), not as a hard failure of the whole mapping."""
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        tbl = Table(table, MetaData(), schema=schema, autoload_with=engine)
        try:
            with engine.begin() as conn:
                if type_ == DataSourceType.postgresql:
                    conn.execute(text(f"SET LOCAL statement_timeout = {timeout * 1000}"))
                elif type_ == DataSourceType.mysql:
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={timeout * 1000}"))
                return int(conn.execute(select(func.count()).select_from(tbl)).scalar() or 0)
        except OperationalError as exc:
            if _looks_like_timeout(exc):
                return None
            raise
    finally:
        engine.dispose()


def execute_probe(
    type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str,
    sql: str, timeout: int,
) -> None:
    """Module 14 §7.2 — the repair loop's real-execution signal: runs `sql` wrapped as a
    read-only, LIMIT-1 probe and raises on any real database error (unknown column, type
    mismatch, malformed GROUP BY, …) that only surfaces at execution time, never mutating
    anything. Returns None on success; the caller decides what a failure means."""
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        inner = sql.strip().rstrip(";")
        # Oracle rejects an unquoted identifier starting with "_" (ORA-00911) — confirmed
        # against a real Oracle source — hence the alias starts with a letter.
        wrapped = f"SELECT * FROM ({inner}) repair_probe_x" + (" WHERE ROWNUM <= 1" if type_ == DataSourceType.oracle else " LIMIT 1")
        try:
            with engine.begin() as conn:
                if type_ == DataSourceType.postgresql:
                    conn.execute(text(f"SET LOCAL statement_timeout = {timeout * 1000}"))
                elif type_ == DataSourceType.mysql:
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={timeout * 1000}"))
                conn.execute(text(wrapped))
        except OperationalError as exc:
            if _looks_like_timeout(exc):
                raise SampleTimeout("Délai dépassé lors de la vérification du SQL.") from exc
            raise
    finally:
        engine.dispose()


_NON_ADDITIVE_NAME_SUFFIXES = ("_id", "_year", "_code", "_number")
_NON_ADDITIVE_NAMES = ("id", "year")


def _infer_column_role(sql_type, column_name: str = "") -> str:
    if isinstance(sql_type, (sa_types.Date, sa_types.DateTime, sa_types.Time)):
        return "temporal"
    if isinstance(sql_type, sa_types.Boolean):
        return "dimension"
    if isinstance(sql_type, (sa_types.Integer, sa_types.Numeric, sa_types.Float)):
        # Identifiers, years, codes: numeric by storage type but never meaningful to SUM/AVG —
        # a real semantic layer treats these as categorical, not additive. Without this, a
        # plain integer "sale_year" or "cust_id" column gets picked as a KPI's measure and
        # produces a nonsensical total (e.g. "Total sale_year" = sum of years).
        lowered = column_name.lower()
        if lowered in _NON_ADDITIVE_NAMES or lowered.endswith(_NON_ADDITIVE_NAME_SUFFIXES):
            return "dimension"
        return "measure"
    return "dimension"


def _stringify_profile_value(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time_)):
        return value.isoformat()
    return str(value)


def profile_table(
    type_: DataSourceType, host: str, port: int, database_name: str | None, username: str, password: str,
    schema: str, table: str,
) -> list[dict]:
    """Column-level profile for indicator suggestion (Module 12) — per column: SQL type,
    inferred role (measure/dimension/temporal), distinct count, null rate, min/max, and for
    low-cardinality columns only, a sample of *distinct values* (never raw rows). One
    aggregate query covers every column's count/distinct/min/max in a single round-trip;
    a second, targeted query per low-cardinality column fetches its distinct-value sample."""
    engine = _sql_engine(type_, host, port, database_name, username, password)
    try:
        metadata = MetaData()
        table_obj = Table(table, metadata, schema=schema, autoload_with=engine)
        cols = list(table_obj.columns)

        agg_exprs = [func.count().label("__total__")]
        for c in cols:
            agg_exprs.append(func.count(distinct(c)).label(f"{c.name}__distinct"))
            agg_exprs.append(func.count(c).label(f"{c.name}__nonnull"))
            agg_exprs.append(func.min(c).label(f"{c.name}__min"))
            agg_exprs.append(func.max(c).label(f"{c.name}__max"))
        stmt = select(*agg_exprs)

        try:
            with engine.begin() as conn:
                if type_ == DataSourceType.postgresql:
                    conn.execute(text(f"SET LOCAL statement_timeout = {PROFILE_TIMEOUT * 1000}"))
                elif type_ == DataSourceType.mysql:
                    conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={PROFILE_TIMEOUT * 1000}"))
                agg_row = conn.execute(stmt).mappings().one()
                total = agg_row["__total__"] or 0

                profiles = []
                for c in cols:
                    distinct_count = agg_row[f"{c.name}__distinct"] or 0
                    nonnull = agg_row[f"{c.name}__nonnull"] or 0
                    null_rate = round(1 - (nonnull / total), 4) if total else 0.0
                    sample_values: list[str] = []
                    filter_enum_values: list[str] | None = None
                    if 0 < distinct_count <= FILTER_ENUM_MAX:
                        # One query serves both needs — sample_values stays exactly the same
                        # (first SAMPLE_VALUES_LIMIT of the same unordered result set) as before
                        # this change, only ever populated below LOW_CARDINALITY_THRESHOLD like
                        # it always was; filter_enum_values additionally keeps the full list,
                        # up to FILTER_ENUM_MAX, whenever that's a complete enumeration.
                        enum_stmt = select(distinct(c)).where(c.isnot(None)).limit(FILTER_ENUM_MAX)
                        filter_enum_values = [_stringify_profile_value(v) for v in conn.execute(enum_stmt).scalars().all()]
                        if distinct_count <= LOW_CARDINALITY_THRESHOLD:
                            sample_values = filter_enum_values[:SAMPLE_VALUES_LIMIT]
                    profiles.append({
                        "name": c.name,
                        "sql_type": str(c.type),
                        "role": _infer_column_role(c.type, c.name),
                        "distinct_count": int(distinct_count),
                        "null_rate": null_rate,
                        "min_value": _stringify_profile_value(agg_row.get(f"{c.name}__min")),
                        "max_value": _stringify_profile_value(agg_row.get(f"{c.name}__max")),
                        "sample_values": sample_values,
                        "filter_enum_values": filter_enum_values,
                    })
        except OperationalError as exc:
            if _looks_like_timeout(exc):
                raise SampleTimeout("Délai dépassé lors du profilage de la table.") from exc
            raise
        return profiles
    finally:
        engine.dispose()
