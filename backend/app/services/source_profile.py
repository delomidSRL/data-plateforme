import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource, DataSourceOrigin, DataSourceType
from app.models.medallion import MedallionDataset, MedallionLayer
from app.models.semantic_annotation import SemanticAnnotation
from app.services import connections

logger = logging.getLogger(__name__)

# Only SQL sources expose the schema.table/column shape the mapping vocabulary (fact/
# reference, measure/temporal/dimension/join_keys) is built around — MinIO's buckets/objects
# have no such structure.
_SQL_TYPES = (DataSourceType.postgresql, DataSourceType.mysql, DataSourceType.oracle)


@dataclass
class ColumnProfile:
    name: str
    sql_type: str
    role: str
    distinct_count: int
    null_rate: float
    min_value: str | None = None
    max_value: str | None = None
    sample_values: list[str] = field(default_factory=list)
    description: str | None = None
    # Module 14 extension "primitives gold" §3.1/§2 — the column's REAL, complete distinct
    # values (never a truncated sample) when its cardinality is low enough; None means either
    # too many distinct values to enumerate, or unknown. The only source a gold filter's
    # literal value is ever validated against — never a fresh scan at filter-validation time.
    filter_enum_values: list[str] | None = None


@dataclass
class TableProfile:
    table: str  # "schema.table"
    source_id: int
    source_name: str
    description: str | None = None
    columns: list[ColumnProfile] = field(default_factory=list)
    # Module 14 §5.2 — the deterministic anchor for evidence.from_profile. None means the
    # COUNT(*) itself timed out (rare, large table) — kept distinct from 0 (a genuinely empty
    # table), since resolve_mapping() treats None as "no anchor available" (anti-loop rule).
    row_count: int | None = None


def _lexical_score(instruction: str, table_name: str, description: str | None) -> int:
    """Cheap keyword-overlap score used only to shrink an oversized candidate list before
    profiling — never used to decide correctness, only which tables are worth the real
    profile query when there are more candidates than `max_tables`."""
    words = {w for w in instruction.lower().split() if len(w) > 2}
    if not words:
        return 0
    haystack = f"{table_name} {description or ''}".lower()
    return sum(1 for w in words if w in haystack)


def _project_bronze_candidates(db: Session, project_id: int, known_sources: dict[int, DataSource]) -> list[tuple[DataSource, str, str]]:
    """Annexe "élargir le scope de l'assistant IA" — a project's OWN already-registered bronze
    datasets (e.g. from a CSV import landed via file_import.py, which writes into the
    platform's own warehouse and is therefore invisible to the plain external-DataSource scan
    below) are real, already-vetted business tables for THIS project, regardless of which
    DataSource/schema they physically live on. Opt-in and narrow: only rows this exact
    project already registered at the bronze layer, never a blind scan of the platform
    warehouse's other schemas (which would also expose every OTHER project's own bronze/
    silver/gold — genuinely wrong to offer as a fresh source)."""
    rows = (
        db.query(MedallionDataset)
        .filter(MedallionDataset.project_id == project_id, MedallionDataset.layer == MedallionLayer.bronze, MedallionDataset.source_id.isnot(None), MedallionDataset.source_object.isnot(None))
        .all()
    )
    out: list[tuple[DataSource, str, str]] = []
    for row in rows:
        source = known_sources.get(row.source_id)
        if source is None:
            source = db.get(DataSource, row.source_id)
            if source is None or source.type not in _SQL_TYPES:
                continue
            known_sources[row.source_id] = source
        if "." not in row.source_object:
            continue
        schema_name, table_name = row.source_object.split(".", 1)
        out.append((source, schema_name, table_name))
    return out


def build_profiles(db: Session, project_id: int | None = None, instruction: str = "", max_tables: int = 40) -> list[TableProfile]:
    """Column-level profile (schema + light stats + annotations) — never raw rows, same rule
    as Module 12's gold_profile. `max_tables` only ever bounds the EXTERNAL-DataSource scan (a
    live connection can genuinely have far more tables than are relevant) — a project's own
    already-registered bronze layer is never truncated, see `from_project_bronze` below; it's
    a human-curated, inherently bounded set already.

    Candidate scope (annexe "élargir le scope de l'assistant IA" — don't mix a project's own
    data with unrelated business domains): when `project_id` is given AND that project already
    has its own registered bronze layer (e.g. from a CSV import, see
    _project_bronze_candidates), candidates are STRICTLY that project's own bronze tables —
    every other external DataSource is deliberately excluded, even ones that would otherwise
    qualify. Mixing a project's own already-defined data with an unrelated external source
    (confirmed with a real project: a hospital dataset ending up alongside an unrelated
    "admission" table from a totally different sales database, purely by name coincidence) is

    Candidate scope (annexe "élargir le scope de l'assistant IA" — don't mix a project's own
    data with unrelated business domains): when `project_id` is given AND that project already
    has its own registered bronze layer (e.g. from a CSV import, see
    _project_bronze_candidates), candidates are STRICTLY that project's own bronze tables —
    every other external DataSource is deliberately excluded, even ones that would otherwise
    qualify. Mixing a project's own already-defined data with an unrelated external source
    (confirmed with a real project: a hospital dataset ending up alongside an unrelated
    "admission" table from a totally different sales database, purely by name coincidence) is
    exactly the kind of contamination this scoping prevents. Only a project with NO bronze of
    its own yet — the traditional "fresh project, pick tables from a live connection" flow —
    falls back to scanning every registered external DataSource."""
    known_sources: dict[int, DataSource] = {}
    candidates: list[tuple[DataSource, str, str]] = []

    if project_id is not None:
        candidates = _project_bronze_candidates(db, project_id, known_sources)
    # A project's own already-registered bronze layer is a human-curated, inherently bounded
    # set (whatever was actually imported for THIS project) — never truncated by max_tables,
    # unlike the external-DataSource scan below (a live connection can genuinely have far more
    # tables than are relevant, hence the lexical pre-filter there). Confirmed on a real
    # project: max_tables=5 was silently dropping 14 of 19 real business tables (patient,
    # admission, billing...) from the AI's shortlist even though every one of them belonged to
    # this exact project.
    from_project_bronze = bool(candidates)

    if not candidates:
        # Platform-origin sources are the warehouse/object-store the platform provisioned for
        # its OWN dbt output (bronze/silver/gold of every existing project) — infrastructure,
        # never a candidate business source to mine for a new project's tables.
        sources = db.query(DataSource).filter(DataSource.type.in_(_SQL_TYPES), DataSource.origin == DataSourceOrigin.external).all()
        known_sources = {s.id: s for s in sources}
        for source in sources:
            try:
                secret = decrypt_secret(source.secret_encrypted)
                result = connections.introspect(source.type, source.host, source.port, source.database_name, source.username, secret, source.options)
            except Exception:
                continue
            for schema in result.schemas:
                for table in schema.tables:
                    candidates.append((source, schema.name, table))

    if not candidates:
        return []

    all_sources = list(known_sources.values())
    annotations = db.query(SemanticAnnotation).filter(SemanticAnnotation.data_source_id.in_([s.id for s in all_sources])).all()
    ann_by_key: dict[tuple[int, str, str | None], str] = {(a.data_source_id, a.table_name, a.column_name): a.description for a in annotations}

    if not from_project_bronze and len(candidates) > max_tables:
        def _score(c: tuple[DataSource, str, str]) -> int:
            full_name = f"{c[1]}.{c[2]}"
            return _lexical_score(instruction, full_name, ann_by_key.get((c[0].id, full_name, None)))

        ranked = sorted(candidates, key=_score, reverse=True)
        # Investigation shortlist (Module 14 correctif §5) — diagnostic only, no behavior
        # change: a table introspected but dropped here is invisible to the AI for the rest of
        # this mapping call. Logging which ones and why (lexical score vs. the instruction,
        # against the hard cap) answers a factual question without guessing — does the source
        # actually have the tables the instruction needs, just excluded by this pre-filter, or
        # are they genuinely absent from the source altogether.
        dropped = ranked[max_tables:]
        if dropped:
            logger.debug(
                "Module 13 pre-filtre (max_tables=%d) : %d table(s) introspectée(s) mais écartée(s) du shortlist (score lexical trop bas) : %s",
                max_tables, len(dropped),
                ", ".join(f"{c[1]}.{c[2]} (score={_score(c)})" for c in dropped),
            )
        candidates = ranked[:max_tables]

    profiles: list[TableProfile] = []
    for source, schema_name, table_name in candidates:
        full_name = f"{schema_name}.{table_name}"
        try:
            secret = decrypt_secret(source.secret_encrypted)
            col_profiles = connections.profile_table(source.type, source.host, source.port, source.database_name, source.username, secret, schema_name, table_name)
        except Exception:
            continue
        columns = [
            ColumnProfile(
                name=c["name"], sql_type=c["sql_type"], role=c["role"],
                distinct_count=c["distinct_count"], null_rate=c["null_rate"],
                min_value=c["min_value"], max_value=c["max_value"], sample_values=c["sample_values"],
                filter_enum_values=c.get("filter_enum_values"),
                description=ann_by_key.get((source.id, full_name, c["name"])),
            )
            for c in col_profiles
        ]
        try:
            rc = connections.row_count(source.type, source.host, source.port, source.database_name, source.username, secret, schema_name, table_name, connections.PROFILE_TIMEOUT)
        except Exception:
            rc = None
        profiles.append(TableProfile(table=full_name, source_id=source.id, source_name=source.name, description=ann_by_key.get((source.id, full_name, None)), columns=columns, row_count=rc))
    return profiles
