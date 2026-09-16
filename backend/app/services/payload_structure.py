"""Module 6 extension (payload & structuration) — §4 (profiling & contract) and §5 (rendering).

Structuration is a **deterministic compiler**: contract x payload -> SQL, never SQL from an
LLM. Every field goes text-first (`payload->>'key'`) then through a guarded, never-raising
cast (`dp_try_cast_*`, a tiny set of PL/pgSQL helpers deployed once per project via
`on-run-start` in `public` — Postgres's own `::type`/`to_date` casts raise a hard error on a
bad value, which would abort the whole model; these helpers catch that and return NULL
instead, exactly the "try_cast-style, never a naked cast" decision in spec §11.7, empirically
confirmed: `to_date('31/02/2024','DD/MM/YYYY')` and `'99999999999999'::integer` both raise in
real Postgres, they don't silently clamp). A row is clean iff every field under
`on_cast_error=quarantine` policy cast cleanly; a field under `null`/`text` policy never
quarantines the row (§0) — its own materialized value differs (NULL vs. raw text) but it can
never gate the split. Rows that fail route to `__quarantine`, tagged with the failing columns,
their raw value and the target type — never silently NULLed, never dropped.
"""
import hashlib
import json
import logging
import re

import psycopg
from psycopg import sql as pgsql
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource, DataSourceType
from app.models.file_import import FileImport, FileImportStatus, ImportMode
from app.models.medallion import MedallionDataset, MedallionLayer
from app.services import connections, schema_infer
from app.services.sql_validator import validate_predicate_sql

logger = logging.getLogger("app.payload_structure")

IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ALLOWED_TARGET_TYPES = {"text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"}
ON_CAST_ERROR_POLICIES = {"quarantine", "null", "text"}

PROFILE_SAMPLE_SIZE = schema_infer.SAMPLE_SIZE  # 1000, same bound as M6's own inference

# schema_infer._THOUSANDS_CHARS is space / no-break space / narrow no-break space — the same
# separators M6's own inference already treats as thousands grouping (French convention).
_THOUSANDS_CHARS = schema_infer._THOUSANDS_CHARS

# Postgres to_char/to_date format tokens are all digit-only fields here, so a straight token
# substitution from the M6 contract's Python strptime-style format is unambiguous.
_PY_TO_PG_DATE_TOKENS = [
    ("%Y", "YYYY"), ("%y", "YY"), ("%m", "MM"), ("%d", "DD"),
    ("%H", "HH24"), ("%M", "MI"), ("%S", "SS"),
]
_DEFAULT_DATE_FORMAT = "%d/%m/%Y"
_DEFAULT_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"


class PayloadStructureError(Exception):
    pass


# ---------------------------------------------------------------------------
# Locating the payload landing table behind a bronze dataset
# ---------------------------------------------------------------------------

def resolve_import(db: Session, dataset: MedallionDataset) -> FileImport | None:
    """The FileImport that landed `dataset`'s payload table, or None if this bronze dataset
    isn't backed by a payload-mode import at all (wrong layer, no source_object, or a typed
    import) — callers treat None as "structuration doesn't apply here", never an error."""
    if dataset.layer != MedallionLayer.bronze or not dataset.source_id or not dataset.source_object:
        return None
    if "." not in dataset.source_object:
        return None
    schema_name, table = dataset.source_object.split(".", 1)
    fi = (
        db.query(FileImport)
        .filter(
            FileImport.target_source_id == dataset.source_id,
            FileImport.target_schema == schema_name,
            FileImport.target_table == table,
        )
        .order_by(FileImport.id.desc())
        .first()
    )
    if fi is None or fi.import_mode != ImportMode.payload:
        return None
    return fi


def bulk_payload_backed(db: Session, datasets: list[MedallionDataset]) -> dict[int, bool]:
    """Which bronze datasets in this list are backed by a payload-mode import — cheap,
    control-plane-only (no warehouse touch), one batched query rather than resolve_import()
    called per dataset. Same (target_source_id, target_schema, target_table) join
    build_lineage_graph already does for origin nodes, kept independent of it here since a
    caller (list_datasets) may not have loaded DataSource rows at all."""
    bronze = [
        d for d in datasets
        if d.layer == MedallionLayer.bronze and d.source_id and d.source_object and "." in d.source_object
    ]
    if not bronze:
        return {}
    source_ids = {d.source_id for d in bronze}
    imports = (
        db.query(FileImport)
        .filter(
            FileImport.target_source_id.in_(source_ids),
            FileImport.status == FileImportStatus.imported,
            FileImport.import_mode == ImportMode.payload,
        )
        .all()
    )
    payload_keys = {(fi.target_source_id, fi.target_schema, fi.target_table) for fi in imports}
    result: dict[int, bool] = {}
    for d in bronze:
        schema_name, table = d.source_object.split(".", 1)
        result[d.id] = (d.source_id, schema_name, table) in payload_keys
    return result


def _connect_warehouse(warehouse: DataSource) -> "psycopg.Connection":
    if warehouse.type != DataSourceType.postgresql:
        raise PayloadStructureError("La structuration exige un warehouse PostgreSQL.")
    secret = decrypt_secret(warehouse.secret_encrypted)
    return psycopg.connect(host=warehouse.host, port=warehouse.port, dbname=warehouse.database_name, user=warehouse.username, password=secret, connect_timeout=10)


def _split_source_object(dataset: MedallionDataset) -> tuple[str, str]:
    schema_name, table = dataset.source_object.split(".", 1)
    if not IDENTIFIER_RE.match(schema_name) or not IDENTIFIER_RE.match(table):
        raise PayloadStructureError("Emplacement du payload invalide.")
    return schema_name, table


# ---------------------------------------------------------------------------
# Étape 2 — profiling (reuses schema_infer.infer_schema verbatim on the union of payload keys)
# ---------------------------------------------------------------------------

def profile_payload(db: Session, dataset: MedallionDataset) -> list[dict]:
    """Samples the first PROFILE_SAMPLE_SIZE rows of the payload table (ordered by the file's
    own row_number, not an arbitrary DB order), unions every key ever seen (captures shape
    drift across reimports), and runs each key through schema_infer — the exact same cascade,
    confidence, and francophone-pitfall table CSV/Excel typed imports already use."""
    fi = resolve_import(db, dataset)
    if fi is None:
        raise PayloadStructureError("Ce dataset bronze n'est pas adossé à un import en mode payload.")
    warehouse = db.get(DataSource, dataset.source_id)
    if warehouse is None:
        raise PayloadStructureError("Source introuvable.")
    schema_name, table = _split_source_object(dataset)

    conn = _connect_warehouse(warehouse)
    try:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL("SELECT payload FROM {}.{} ORDER BY row_number LIMIT {}").format(
                    pgsql.Identifier(schema_name), pgsql.Identifier(table), pgsql.Literal(PROFILE_SAMPLE_SIZE),
                )
            )
            payload_rows = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()

    if not payload_rows:
        raise PayloadStructureError("Aucune ligne dans la table payload — importez d'abord un fichier.")

    keys: list[str] = []
    seen: set[str] = set()
    for row in payload_rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)

    mapping = schema_infer.infer_schema(payload_rows, keys)
    for entry in mapping:
        entry["on_cast_error"] = "quarantine"
    return mapping


# ---------------------------------------------------------------------------
# Contract validation (Étape 2, §4.4) — identifiers + AST-checked cast expressions
# ---------------------------------------------------------------------------

def _pg_date_format(py_format: str) -> str:
    fmt = py_format
    for py_tok, pg_tok in _PY_TO_PG_DATE_TOKENS:
        fmt = fmt.replace(py_tok, pg_tok)
    return fmt


def _jinja_string_literal(s: str) -> str:
    """Escape for embedding as a Jinja string literal inside `{{ config(...) }}` — a
    different trust boundary than _sql_string_literal below: this text is parsed by dbt's
    Jinja engine, not Postgres, and it can contain BOTH single quotes (the RAISE message's own
    SQL string literal) and double quotes (every quoted identifier in the rendered SQL), so
    neither bare quote char is safe as a delimiter without escaping — Python/Jinja-style
    backslash escaping handles both uniformly."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sql_string_literal(s: str) -> str:
    """Single-quote a string for SQL embedding — the date/timestamp format is the one contract
    parameter that's a raw string rather than an already-identifier-safe name (mirrors
    quality_intrinsic._sql_string_literal, same trust boundary)."""
    return "'" + s.replace("'", "''") + "'"


def _clean_numeric_expr(txt_expr: str) -> str:
    """Strip thousands separators, normalize the French decimal comma to a dot — a pure text
    transform, never fails, so it's safe to embed directly (no try_cast needed for this part)."""
    return f"regexp_replace(replace({txt_expr}, ',', '.'), '[{_THOUSANDS_CHARS}]', '', 'g')"


def field_cast_expr(field: dict, txt_expr: str) -> str:
    """The guarded cast expression for one field, operating on `txt_expr` (either
    `payload->>'key'` at contract-validation time, or an already-extracted text column alias
    at render time — same builder, same guarantee: never raises, regardless of input."""
    target_type = field["target_type"]
    if target_type == "text":
        return txt_expr
    if target_type == "jsonb":
        return f"dp_try_cast_jsonb({txt_expr})"
    if target_type in ("integer", "bigint"):
        fn = "dp_try_cast_integer" if target_type == "integer" else "dp_try_cast_bigint"
        return f"{fn}({_clean_numeric_expr(txt_expr)})"
    if target_type == "numeric":
        return f"dp_try_cast_numeric({_clean_numeric_expr(txt_expr)})"
    if target_type == "boolean":
        return f"dp_try_cast_boolean({txt_expr})"
    if target_type == "date":
        fmt = _pg_date_format(field.get("format") or _DEFAULT_DATE_FORMAT)
        return f"dp_try_cast_date({txt_expr}, {_sql_string_literal(fmt)})"
    if target_type == "timestamp":
        fmt = _pg_date_format(field.get("format") or _DEFAULT_TIMESTAMP_FORMAT)
        return f"dp_try_cast_timestamp({txt_expr}, {_sql_string_literal(fmt)})"
    raise PayloadStructureError(f"Type cible non supporté : « {target_type} ».")


def validate_column_mapping(column_mapping: list[dict]) -> None:
    """§4.4 — every included field's target_name/target_type validated, and its cast
    expression run through sql_validator (M14) as a static AST safety net: SELECT-only, no
    subquery/table, only whitelisted functions, referencing only `payload` (§2's hygiene rule
    even though the expression here is entirely contract-built, never free user SQL)."""
    seen_names: set[str] = set()
    for field in column_mapping:
        if not field.get("include", True):
            continue
        source_name = field.get("source_name")
        target_name = field.get("target_name")
        target_type = field.get("target_type")
        on_cast_error = field.get("on_cast_error", "quarantine")
        if not source_name:
            raise PayloadStructureError("Chaque champ inclus doit référencer une clé du payload.")
        if not target_name or not IDENTIFIER_RE.match(target_name):
            raise PayloadStructureError(f"Nom de colonne invalide : « {target_name} ».")
        if target_name in seen_names:
            raise PayloadStructureError(f"Colonne cible en double : « {target_name} ».")
        seen_names.add(target_name)
        if target_type not in ALLOWED_TARGET_TYPES:
            raise PayloadStructureError(f"Type cible non supporté : « {target_type} ».")
        if on_cast_error not in ON_CAST_ERROR_POLICIES:
            raise PayloadStructureError(f"Politique d'erreur invalide : « {on_cast_error} ».")

        expr = field_cast_expr(field, f"(payload->>{_sql_string_literal(source_name)})")
        result = validate_predicate_sql(expr, "postgres", available_columns={"payload"})
        if not result.valid:
            raise PayloadStructureError(f"Expression de cast invalide pour « {target_name} » : {'; '.join(result.errors)}")

    if not seen_names:
        raise PayloadStructureError("Au moins un champ doit être inclus.")


# ---------------------------------------------------------------------------
# Étape 3 — rendering (§5.2/§5.3): two dbt models per bronze payload dataset, both landing in
# the `bronze` schema next to `<name>` itself — `__parsed` (view, the clean output silver
# reads via ref()) and `__quarantine` (table, so the rows are actually inspectable/queryable).
# ---------------------------------------------------------------------------

# Deployed once per project via dbt's on-run-start (dbt_project.py), in `public` — always on
# Postgres's default search_path, so the generated model SQL below can call them unqualified.
# Each catches every exception and returns NULL instead — the one thing a naked `::type` or
# to_date()/to_timestamp() cast in Postgres will never do (empirically confirmed: both raise
# hard errors on a bad value, aborting the whole statement — see module docstring).
TRY_CAST_FUNCTIONS_SQL = """
CREATE OR REPLACE FUNCTION dp_try_cast_integer(v text) RETURNS integer AS $$
BEGIN
    RETURN v::integer;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_bigint(v text) RETURNS bigint AS $$
BEGIN
    RETURN v::bigint;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_numeric(v text) RETURNS numeric AS $$
BEGIN
    RETURN v::numeric;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_boolean(v text) RETURNS boolean AS $$
BEGIN
    RETURN CASE lower(btrim(v))
        WHEN 'true' THEN true WHEN '1' THEN true WHEN 'oui' THEN true WHEN 'vrai' THEN true WHEN 'o' THEN true WHEN 'yes' THEN true WHEN 'y' THEN true
        WHEN 'false' THEN false WHEN '0' THEN false WHEN 'non' THEN false WHEN 'faux' THEN false WHEN 'n' THEN false WHEN 'no' THEN false
        ELSE NULL
    END;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_date(v text, fmt text) RETURNS date AS $$
BEGIN
    RETURN to_date(v, fmt);
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_timestamp(v text, fmt text) RETURNS timestamp AS $$
BEGIN
    RETURN to_timestamp(v, fmt);
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_jsonb(v text) RETURNS jsonb AS $$
BEGIN
    IF v IS NULL THEN RETURN NULL; END IF;
    RETURN v::jsonb;
EXCEPTION WHEN OTHERS THEN RETURN to_jsonb(v);
END;
$$ LANGUAGE plpgsql;
""".strip()


def render_models(
    column_mapping: list[dict], bronze_name: str,
    quarantine_policy: str = "report", quarantine_threshold_pct: float | None = None,
) -> dict[str, str]:
    """Contract -> SQL, deterministic (§5.4 idempotence: an unchanged contract renders byte-
    identical SQL). Returns {"parsed_sql": ..., "quarantine_sql": ...} — two dbt model files,
    both reading `{{ source('bronze', bronze_name) }}` (the existing, unchanged bronze
    ingestion — §11.1), never `imports.<table>` directly (dbt has no source declared there).

    `quarantine_policy="block"` (§7.3, opt-in — default "report" never blocks) adds a
    post_hook to `__parsed` that recomputes the quarantine rate from the SAME extracted/typed/
    flagged CTEs (not by querying the sibling `__quarantine` model/table — dbt gives no
    ordering guarantee between two models that don't `ref()` each other, so a cross-model
    check could race) and raises if it exceeds the threshold, failing that model's build."""
    included = [f for f in column_mapping if f.get("include", True)]
    if not included:
        raise PayloadStructureError("Le contrat de structuration n'a aucun champ inclus.")

    extracted_cols = ["row_number", "source_file", "load_id", "payload"]
    typed_cols = ["row_number", "source_file", "load_id", "payload"]
    parsed_output_cols = ["row_number", "source_file", "load_id"]
    fail_flags: list[str] = []
    failure_pairs: list[str] = []

    for field in included:
        target = field["target_name"]
        source = field["source_name"]
        policy = field.get("on_cast_error", "quarantine")
        txt_alias = f"{target}_txt"

        # NULLIF(btrim(...), '') here — not just for missing keys — mirrors the existing M6
        # typed-mode cast_value() convention exactly: a blank cell is NULL for every target
        # type, including text (cast_value strips+blank-checks before its type branch, even
        # for "text"). Doing it once here keeps every cast branch below simple and correct.
        extracted_cols.append(f"NULLIF(btrim(payload->>{_sql_string_literal(source)}), '') AS \"{txt_alias}\"")
        # Carried through `typed` too — `flagged`'s row_ok/failures (§ next CTE) still need
        # the raw text to tell "legitimately blank" apart from "failed to cast".
        typed_cols.append(f'"{txt_alias}"')

        if policy == "text":
            typed_expr = f'"{txt_alias}"'
        else:
            typed_expr = field_cast_expr(field, f'"{txt_alias}"')
            # Same AST safety net as contract-save time (§4.4/§5.3) — never rendered
            # unvalidated, even though it was already checked once when the contract was PUT.
            result = validate_predicate_sql(typed_expr, "postgres", available_columns={txt_alias})
            if not result.valid:
                raise PayloadStructureError(f"Expression de cast rejetée pour « {target} » : {'; '.join(result.errors)}")
        typed_cols.append(f'{typed_expr} AS "{target}"')
        parsed_output_cols.append(f'"{target}"')

        if policy == "quarantine":
            # `_txt` is already NULL for a blank cell (see extracted_cols above), so this is
            # exactly "there was a real value and the cast still couldn't make sense of it".
            fail_flag = f'("{txt_alias}" IS NOT NULL AND "{target}" IS NULL)'
            fail_flags.append(fail_flag)
            failure_pairs.append(
                _sql_string_literal(target) + ", CASE WHEN " + fail_flag + " THEN jsonb_build_object("
                "'valeur_brute', \"" + txt_alias + "\", 'type_cible', " + _sql_string_literal(field["target_type"]) + ", "
                "'motif', 'cast_echoue') END"
            )

    row_ok_expr = "NOT (" + " OR ".join(fail_flags) + ")" if fail_flags else "true"
    failures_expr = "jsonb_strip_nulls(jsonb_build_object(" + ", ".join(failure_pairs) + "))" if failure_pairs else "'{}'::jsonb"

    # A plain SELECT can't reference a sibling alias from its own list (Postgres rejects
    # "nom" inside the very expression that defines it) — row_ok/failures need `typed`'s cast
    # columns already resolved as real columns, hence a third CTE rather than one more column
    # bolted onto `typed` itself.
    def _build_with(from_clause: str) -> str:
        return (
            "WITH extracted AS (\n    SELECT " + ",\n        ".join(extracted_cols) + "\n"
            "    FROM " + from_clause + "\n"
            "),\ntyped AS (\n    SELECT " + ",\n        ".join(typed_cols) + "\n    FROM extracted\n"
            "),\nflagged AS (\n    SELECT *,\n"
            "        (" + row_ok_expr + ") AS __dp_row_ok,\n"
            "        (" + failures_expr + ") AS __dp_failures\n    FROM typed\n)\n"
        )

    with_clause = _build_with("{{ source('bronze', '" + bronze_name + "') }}")

    parsed_config = "materialized='view', schema='bronze'"
    if quarantine_policy == "block" and quarantine_threshold_pct is not None:
        threshold_ratio = quarantine_threshold_pct / 100
        # The post_hook is executed as a plain, already-Jinja-rendered SQL string (dbt never
        # re-parses a config() argument's own text for {{ }} tags — they'd stay literal), so
        # this copy of the WITH clause can't use {{ source(...) }} like the model body does;
        # it references the physical bronze table directly instead — same read, same schema.
        with_clause_literal = _build_with(f'bronze."{bronze_name}"')
        # Postgres RAISE format strings treat every bare `%` as a positional placeholder
        # (a literal one needs `%%`) — sidestepped entirely by spelling "pourcent" instead of
        # using the symbol, so there is exactly one `%` in this string, for the one arg passed.
        indented_with = "\n".join("    " + line for line in with_clause_literal.rstrip("\n").splitlines())
        guard_sql = (
            "DO $$\nDECLARE total_rows bigint; clean_rows bigint;\nBEGIN\n"
            "    SELECT count(*), count(*) FILTER (WHERE __dp_row_ok) INTO total_rows, clean_rows\n"
            "    FROM (\n" + indented_with + "\n        SELECT __dp_row_ok FROM flagged\n    ) t;\n"
            f"    IF total_rows > 0 AND (total_rows - clean_rows)::numeric / total_rows > {threshold_ratio} THEN\n"
            f"        RAISE EXCEPTION 'Structuration bloquee pour {bronze_name} : taux de quarantaine % pourcent (seuil {quarantine_threshold_pct:g} pourcent)', "
            "round(100.0 * (total_rows - clean_rows) / total_rows, 2);\n"
            "    END IF;\nEND $$;"
        )
        parsed_config += f", post_hook={_jinja_string_literal(guard_sql)}"

    parsed_sql = (
        "{{ config(" + parsed_config + ") }}\n\n" + with_clause
        + "SELECT " + ", ".join(parsed_output_cols) + "\nFROM flagged\nWHERE __dp_row_ok\n"
    )
    quarantine_sql = (
        "{{ config(materialized='table', schema='bronze') }}\n\n" + with_clause
        + "SELECT row_number, source_file, load_id, __dp_failures AS failures, payload\n"
        "FROM flagged\nWHERE NOT __dp_row_ok\n"
    )
    return {"parsed_sql": parsed_sql, "quarantine_sql": quarantine_sql}


# ---------------------------------------------------------------------------
# Étape 4 — quarantine inspection (reads the materialized `bronze.<name>__quarantine` table
# directly; no control-plane copy of the data ever exists, same relay-not-store principle as
# the M10 preview / M11 export).
# ---------------------------------------------------------------------------

def _quarantine_table_exists(conn, bronze_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'bronze' AND table_name = %s",
            (f"{bronze_name}__quarantine",),
        )
        return cur.fetchone() is not None


def list_quarantine(warehouse: DataSource, bronze_name: str, column: str | None, limit: int, offset: int) -> list[dict]:
    if not IDENTIFIER_RE.match(bronze_name):
        raise PayloadStructureError("Nom de dataset invalide.")
    table = f"{bronze_name}__quarantine"
    conn = _connect_warehouse(warehouse)
    try:
        if not _quarantine_table_exists(conn, bronze_name):
            return []
        with conn.cursor() as cur:
            if column:
                cur.execute(
                    pgsql.SQL("SELECT row_number, source_file, failures, payload FROM bronze.{} WHERE failures ? %s ORDER BY row_number LIMIT %s OFFSET %s")
                    .format(pgsql.Identifier(table)),
                    (column, limit, offset),
                )
            else:
                cur.execute(
                    pgsql.SQL("SELECT row_number, source_file, failures, payload FROM bronze.{} ORDER BY row_number LIMIT %s OFFSET %s")
                    .format(pgsql.Identifier(table)),
                    (limit, offset),
                )
            rows = cur.fetchall()
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()
    return [{"row_number": r[0], "source_file": r[1], "failures": r[2], "payload": r[3]} for r in rows]


def quarantine_summary(warehouse: DataSource, bronze_name: str) -> dict:
    """§6.2 — per-column rejection counts + a few raw examples, to point the engineer at
    *which* field to fix first (e.g. "montant: 214 rejets, tous du type 1 249,90")."""
    if not IDENTIFIER_RE.match(bronze_name):
        raise PayloadStructureError("Nom de dataset invalide.")
    table = f"{bronze_name}__quarantine"
    conn = _connect_warehouse(warehouse)
    try:
        if not _quarantine_table_exists(conn, bronze_name):
            return {"total": 0, "by_column": []}
        with conn.cursor() as cur:
            cur.execute(pgsql.SQL("SELECT count(*) FROM bronze.{}").format(pgsql.Identifier(table)))
            total = cur.fetchone()[0]
            cur.execute(
                pgsql.SQL(
                    "SELECT kv.key, count(*), (array_agg(kv.value->>'valeur_brute'))[1:3] "
                    "FROM bronze.{} q, jsonb_each(q.failures) AS kv(key, value) "
                    "GROUP BY kv.key ORDER BY count(*) DESC"
                ).format(pgsql.Identifier(table))
            )
            by_column = [
                {"column": r[0], "count": r[1], "sample_values": [v for v in (r[2] or []) if v is not None]}
                for r in cur.fetchall()
            ]
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()
    return {"total": total, "by_column": by_column}


def canonical_contract(column_mapping: list[dict], quarantine_policy: str, quarantine_threshold_pct: float | None) -> str:
    minimal = {
        "fields": [
            {"source_name": c["source_name"], "target_name": c["target_name"], "target_type": c["target_type"],
             "format": c.get("format"), "include": c.get("include", True), "on_cast_error": c.get("on_cast_error", "quarantine")}
            for c in column_mapping
        ],
        "quarantine_policy": quarantine_policy,
        "quarantine_threshold_pct": quarantine_threshold_pct,
    }
    return hashlib.sha256(json.dumps(minimal, sort_keys=True).encode()).hexdigest()
