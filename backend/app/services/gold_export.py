"""Module 11 extension — CSV export of a gold table, streamed from the warehouse.

Same read-only relay as the Module 10 preview (`services/preview.py`), pushed from a bounded
sample to the whole table: `COPY (SELECT * FROM <gold>.<table>) TO STDOUT` piped block-by-block
into a `StreamingResponse`. Nothing is buffered in memory, written to disk, or logged by the
control plane — only the export metadata is (`ExportLog`). Gold only; identifiers come from
platform metadata and are quoted with `psycopg.sql.Identifier`, never an f-string (§2/§5)."""
import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import psycopg
from psycopg import sql
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.db.session import SessionLocal
from app.models.data_source import DataSource, DataSourceType
from app.models.export_log import ExportKind, ExportLog
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.services import connections
from app.services.medallion_stats import SCHEMA_BY_LAYER, table_name

logger = logging.getLogger("app.gold_export")

_CONNECT_TIMEOUT = 10
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Deliberately vague, layer-agnostic message: an export.csv resource simply "doesn't exist"
# for a non-gold dataset or a project the caller doesn't own — never reveal which (§2/§3).
_NOT_FOUND = "Ressource d'export introuvable."


class GoldExportError(Exception):
    """Raised only *before* the stream starts — safe to surface as an HTTP status."""

    def __init__(self, message: str, status_code: int = 404):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ExportPlan:
    """Everything the streaming generator needs — plain values only. The request-scoped ORM
    session (and its DataSource row) is closed before the StreamingResponse body runs, so
    nothing lazy-loadable may cross into `stream_csv` (DetachedInstanceError otherwise)."""
    host: str
    port: int
    dbname: str | None
    user: str
    password: str
    schema_name: str
    table: str


def _warehouse(db: Session, project: MedallionProject) -> DataSource:
    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None or warehouse.type != DataSourceType.postgresql:
        raise GoldExportError(_NOT_FOUND, 404)
    return warehouse


def _resolve_target(dataset: MedallionDataset) -> tuple[str, str]:
    if dataset.layer != MedallionLayer.gold:
        # Export is a *restitution* capability — not raw access. Silver/bronze are out of
        # scope in v1 (§0/§7): 404, the resource doesn't exist for them.
        raise GoldExportError(_NOT_FOUND, 404)
    table = table_name(dataset)
    if not table:
        raise GoldExportError("Cette table gold n'est pas encore matérialisée.", 404)
    return SCHEMA_BY_LAYER[MedallionLayer.gold], table


def preflight(db: Session, project: MedallionProject, dataset: MedallionDataset) -> ExportPlan:
    """Resolve + probe the target while we can still return a real HTTP status (before the
    200 + first CSV byte goes out). Snapshots the warehouse connection into plain values."""
    schema, table = _resolve_target(dataset)
    warehouse = _warehouse(db, project)
    secret = decrypt_secret(warehouse.secret_encrypted)
    try:
        exists = connections.table_exists(
            warehouse.type, warehouse.host, warehouse.port, warehouse.database_name,
            warehouse.username, secret, schema, table,
        )
    except Exception as exc:  # noqa: BLE001 — any driver failure is "warehouse unreachable"
        raise GoldExportError(connections.clean_error(exc), 502) from exc
    if not exists:
        raise GoldExportError("Cette table gold n'est pas encore matérialisée.", 404)
    return ExportPlan(
        host=warehouse.host, port=warehouse.port, dbname=warehouse.database_name,
        user=warehouse.username, password=secret, schema_name=schema, table=table,
    )


def filename_for(dataset: MedallionDataset) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    stem = _FILENAME_UNSAFE.sub("_", dataset.name or "").strip("_") or "export"
    return f"{stem}_{stamp}.csv"


def client_ip_of(request) -> str | None:
    """First hop of X-Forwarded-For (the platform sits behind pfSense / a reverse proxy in
    prod), else the socket peer. Validated as an IP so a spoofed header can't inject junk."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
    elif request.client:
        candidate = request.client.host
    else:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def stream_csv(plan: ExportPlan, on_complete: Callable[[int | None], None] | None = None):
    """Generator yielding CSV bytes straight from `COPY … TO STDOUT`. One libpq block is held
    at a time — the whole table is never in the control plane's memory (§0/§5). The wrapping
    transaction is `READ ONLY`: the SELECT under the COPY can touch nothing but the gold table."""
    copy_stmt = sql.SQL("COPY (SELECT * FROM {}.{}) TO STDOUT WITH (FORMAT CSV, HEADER)").format(
        sql.Identifier(plan.schema_name), sql.Identifier(plan.table),
    )
    conn = psycopg.connect(
        host=plan.host, port=plan.port, dbname=plan.dbname,
        user=plan.user, password=plan.password, connect_timeout=_CONNECT_TIMEOUT,
    )
    row_count: int | None = None
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            with cur.copy(copy_stmt) as copy:
                for block in copy:
                    yield bytes(block)
            row_count = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None
        conn.rollback()  # read-only — nothing to commit
    finally:
        conn.close()
    if on_complete is not None:
        try:
            on_complete(row_count)
        except Exception:  # noqa: BLE001 — a journalling failure must never surface here
            logger.warning("export post-stream hook failed", exc_info=True)


def log_export(
    db: Session, project_id: int, dataset_id: int | None, user_id: int | None, client_ip: str | None,
    kind: ExportKind = ExportKind.csv,
) -> int | None:
    """Best-effort journal line at stream open. Returns the new row id, or None on failure —
    never raises: an unwritable journal must not abort the download (§3.6/§6 DoD).

    Module 16 extension §6 — reused as-is for the dbt project export (`kind=dbt_project`,
    `dataset_id=None` — project-scoped, exactly what this table's own docstring already
    anticipated: "whole dbt project later"), no new audit table."""
    try:
        row = ExportLog(
            project_id=project_id, dataset_id=dataset_id, exported_by=user_id,
            kind=kind, row_count=None, client_ip=client_ip,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("ExportLog write failed (project=%s dataset=%s)", project_id, dataset_id, exc_info=True)
        return None


def set_export_row_count(export_log_id: int | None, row_count: int | None) -> None:
    """Fill in `row_count` once the COPY has finished. Its own short-lived session — by the
    time the stream generator runs, the request-scoped session is long closed."""
    if export_log_id is None or row_count is None:
        return
    db = SessionLocal()
    try:
        row = db.get(ExportLog, export_log_id)
        if row is not None:
            row.row_count = row_count
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("ExportLog row_count update failed (id=%s)", export_log_id, exc_info=True)
    finally:
        db.close()
