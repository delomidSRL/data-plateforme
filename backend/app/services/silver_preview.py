"""Module 14 §7.2 — the repair loop's "preview dbt (rendu sans run)" + "aperçu lecture Module
10 (LIMIT, timeout)" steps, combined into one real, bounded, read-only execution check.

Why this can't be Module 10's own preview.py: at plan-generation time (POST /plan, before the
Module 13 human gate), NO bronze table has been materialized in the warehouse yet — bronze
ingestion is the FIRST task of the Airflow DAG, which only runs after the engineer approves the
plan and the project is built (services/pipeline_execute.py). A silver's proposed SQL reads
from {{ source('bronze', ...) }}, which resolves to a warehouse table that doesn't exist yet —
there is nothing there to preview against.

What this does instead: when every bronze table a candidate silver SQL references comes from
the SAME external data source (the common, realistic case here — Étape 1's relation detection
and Étape 3's deterministic join detection already scope themselves to single-source star
schemas the same way), the {{ source('bronze', 'X') }} / {{ ref('Y') }} placeholders are
rewritten to the REAL schema.table on that external source, and the resulting SQL is run as a
bounded, read-only LIMIT-1 probe directly against it — genuine execution feedback (a real typo,
a real type mismatch) using real data, without needing the warehouse to be pre-populated.

Outside that case (bronze tables spanning more than one source, or a ref() to another
not-yet-materialized silver), no real table is available anywhere to check against — this step
is explicitly SKIPPED (never silently treated as "ok"), and the repair loop falls back to the
AST validator (services/sql_validator.py) as its only signal for that silver."""
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource
from app.services import connections

PREVIEW_PROBE_TIMEOUT = 8

_SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"]bronze['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_REF_RE = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")


@dataclass
class PreviewCheck:
    status: str  # "ok" | "error" | "skipped"
    message: str | None = None


def check_silver_sql(db: Session, mapping_tables: list[dict], bronze_names: dict[str, str], sql: str) -> PreviewCheck:
    if _REF_RE.search(sql):
        return PreviewCheck(status="skipped", message="référence un autre modèle silver — pas de table réelle disponible avant construction.")

    referenced = set(_SOURCE_RE.findall(sql))
    if not referenced:
        return PreviewCheck(status="skipped")

    table_by_bname = {bronze_names[t["table"]]: t for t in mapping_tables if t["table"] in bronze_names}
    involved = []
    for bname in referenced:
        t = table_by_bname.get(bname)
        if t is None:
            return PreviewCheck(status="skipped")
        involved.append(t)

    source_ids = {t["source_id"] for t in involved}
    if len(source_ids) != 1:
        return PreviewCheck(status="skipped", message="jointure entre plusieurs sources externes — pas de vérification réelle possible avant ingestion.")

    source = db.get(DataSource, next(iter(source_ids)))
    if source is None:
        return PreviewCheck(status="skipped")

    real_table_by_bname = {bronze_names[t["table"]]: t["table"] for t in involved}

    def _sub(m: re.Match) -> str:
        return real_table_by_bname.get(m.group(1), m.group(1))

    rewritten = _SOURCE_RE.sub(_sub, sql)

    try:
        secret = decrypt_secret(source.secret_encrypted)
        connections.execute_probe(source.type, source.host, source.port, source.database_name, source.username, secret, rewritten, PREVIEW_PROBE_TIMEOUT)
    except connections.SampleTimeout as exc:
        return PreviewCheck(status="error", message=str(exc))
    except Exception as exc:
        return PreviewCheck(status="error", message=connections.clean_error(exc))
    return PreviewCheck(status="ok")
