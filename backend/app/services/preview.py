from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource, DataSourceType
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.services import connections
from app.services.medallion_stats import SCHEMA_BY_LAYER, table_name

PREVIEW_MAX_ROWS = 200
PREVIEW_DEFAULT_LIMIT = 50
CELL_TRUNCATE_LENGTH = 500


@dataclass
class PreviewTarget:
    kind: str  # "materialized" | "source"
    schema_name: str
    table: str
    source_name: str


@dataclass
class PreviewOutcome:
    status: str  # ok | not_materialized | not_found | unreachable | timeout
    message: str | None = None
    columns: list[dict] | None = None
    rows: list[dict] | None = None
    truncated: bool = False
    target: PreviewTarget | None = None


def _serialize_cell(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<binaire — {len(value)} octets>"
    text_ = value if isinstance(value, str) else str(value)
    if len(text_) > CELL_TRUNCATE_LENGTH:
        return text_[:CELL_TRUNCATE_LENGTH] + "…"
    return text_


def _source_target(db: Session, dataset: MedallionDataset) -> tuple[DataSource | None, str | None, str | None]:
    """Where a bronze dataset's *upstream* table lives — the SGBD source it was declared
    against (`source_id` + `source_object`), never the platform's own warehouse. MinIO/file
    objects have no SQL table to sample and are out of v1 scope (spec §7/§8)."""
    if not dataset.source_id or not dataset.source_object:
        return None, None, None
    source = db.get(DataSource, dataset.source_id)
    if source is None or source.type == DataSourceType.minio or "." not in dataset.source_object:
        return source, None, None
    schema_name, table = dataset.source_object.split(".", 1)
    return source, schema_name, table


def attempt_sample(source: DataSource | None, schema_name: str | None, table: str | None, kind: str, limit: int, offset: int) -> PreviewOutcome | None:
    """Try to sample one candidate target. Returns None (not an error) when the table simply
    doesn't exist yet there — the caller decides whether to fall back to another candidate or
    report `not_materialized`. Public (not dataset-scoped): also reused by the structuration
    preview route to sample silver.typed_<name>/silver.structured_<name>, which aren't
    MedallionDataset rows and so never go through resolve_and_sample below."""
    if source is None or not schema_name or not table:
        return None
    try:
        secret = decrypt_secret(source.secret_encrypted)
        exists = connections.table_exists(source.type, source.host, source.port, source.database_name, source.username, secret, schema_name, table)
    except Exception as exc:
        return PreviewOutcome(status="unreachable", message=connections.clean_error(exc))
    if not exists:
        return None
    try:
        # Fetch one extra row to detect "there's more" without a separate count(*) — the
        # spec explicitly rules out count(*) previews (that's Module 5's job).
        result = connections.sample_rows(source.type, source.host, source.port, source.database_name, source.username, secret, schema_name, table, limit + 1, offset)
    except connections.SampleTimeout as exc:
        return PreviewOutcome(status="timeout", message=str(exc))
    except Exception as exc:
        return PreviewOutcome(status="unreachable", message=connections.clean_error(exc))

    truncated = len(result.rows) > limit
    raw_rows = result.rows[:limit]
    rows = [{k: _serialize_cell(v) for k, v in row.items()} for row in raw_rows]
    target = PreviewTarget(kind=kind, schema_name=schema_name, table=table, source_name=source.name)
    return PreviewOutcome(status="ok", columns=result.columns, rows=rows, truncated=truncated, target=target)


def resolve_and_sample(db: Session, project: MedallionProject, dataset: MedallionDataset, limit: int, offset: int, prefer_source: bool = False) -> PreviewOutcome:
    """Orchestrates étape 1 (materialized output) and étape 2 (upstream source) of Module 10.
    `prefer_source=True` is for the *origin* node — it always shows the raw upstream table,
    regardless of whether the bronze dataset it feeds has been built yet."""
    limit = max(1, min(limit, PREVIEW_MAX_ROWS))

    if prefer_source:
        source, schema_name, table = _source_target(db, dataset)
        outcome = attempt_sample(source, schema_name, table, "source", limit, offset)
        if outcome is not None:
            return outcome
        if source is None:
            return PreviewOutcome(status="not_found", message="Aucune source configurée pour ce dataset.")
        return PreviewOutcome(status="not_materialized")

    schema_name = SCHEMA_BY_LAYER[dataset.layer]
    table = table_name(dataset)
    warehouse = db.get(DataSource, project.warehouse_source_id)
    outcome = attempt_sample(warehouse, schema_name, table, "materialized", limit, offset)
    if outcome is not None:
        return outcome
    if warehouse is None:
        return PreviewOutcome(status="not_found", message="Aucun warehouse configuré pour ce projet.")

    # Not materialized yet — for bronze, the upstream source is still worth showing (étape 2).
    if dataset.layer == MedallionLayer.bronze and dataset.source_id:
        source, src_schema, src_table = _source_target(db, dataset)
        source_outcome = attempt_sample(source, src_schema, src_table, "source", limit, offset)
        if source_outcome is not None:
            return source_outcome

    return PreviewOutcome(status="not_materialized")
