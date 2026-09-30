"""Module 19 étape 5 §7.2 — before a code-modified project builds, what a column change would
silently break downstream: other dbt models (real lineage, not a guess), Superset publications
and their charts (Module 11/12), DataQualityCheck (Module 16), CSV exports (Module 11 ext).
Read-only and informational — this module invents no new gate (§7.3): only a Module 16 check
already set to "Bloquer" blocks anything; everything found here is surfaced for an explicit
confirmation, never blocks by itself."""
import re
from dataclasses import dataclass, field

import sqlglot
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlglot import exp

from app.core.security import decrypt_secret
from app.models.data_quality import DataQualityCheck
from app.models.dashboard_spec import DashboardSpec
from app.models.data_source import DataSource
from app.models.export_log import ExportKind, ExportLog
from app.models.medallion import MedallionDataset, MedallionProject, MedallionVersion
from app.models.project_file import ProjectFile
from app.models.superset_publication import SupersetPublication
from app.services import connections, dbt_runner, workspace

_SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_REF_RE = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_CONFIG_RE = re.compile(r"\{\{\s*config\([^)]*\)\s*\}\}")


@dataclass
class ImpactItem:
    severity: str  # "column_removed" | "column_renamed" | "downstream_model" | "superset" | "quality_check" | "export"
    message: str


@dataclass
class ModelImpact:
    dataset_id: int
    dataset_name: str
    path: str
    columns_removed: list[str] = field(default_factory=list)
    columns_added: list[str] = field(default_factory=list)
    items: list[ImpactItem] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id, "dataset_name": self.dataset_name, "path": self.path,
            "columns_removed": self.columns_removed, "columns_added": self.columns_added,
            "items": [{"severity": i.severity, "message": i.message} for i in self.items],
        }


@dataclass
class ImpactResult:
    models: list[ModelImpact] = field(default_factory=list)

    @property
    def has_impact(self) -> bool:
        return any(m.items for m in self.models)


def _stripped_sql(raw_content: str) -> str:
    """Same substitution M14's sql_validator uses (source()/ref() -> harmless placeholder
    table names) so sqlglot parses real SQL instead of Jinja — simplified here to the one
    thing this module needs: the SELECT list's own output column names."""
    sql = _CONFIG_RE.sub("", raw_content)
    sql = _SOURCE_RE.sub(lambda m: f"__src_{m.group(2)}", sql)
    sql = _REF_RE.sub(lambda m: f"__ref_{m.group(1)}", sql)
    return sql


def _static_output_columns(raw_content: str) -> set[str] | None:
    try:
        tree = sqlglot.parse_one(_stripped_sql(raw_content), read="postgres")
    except Exception:
        return None
    if not isinstance(tree, exp.Select) or any(isinstance(e, exp.Star) for e in tree.expressions):
        return None
    names = {e.alias_or_name for e in tree.expressions if e.alias_or_name and e.alias_or_name != "*"}
    return names or None


def _live_output_columns(warehouse: DataSource, compiled_sql: str) -> set[str] | None:
    """§7.2's fallback — only reachable for the CURRENT (compiled) side; the active version's
    own past SQL is never re-run live (it may reference models since renamed/removed)."""
    try:
        engine = connections._sql_engine(
            warehouse.type, warehouse.host, warehouse.port, warehouse.database_name,
            warehouse.username, decrypt_secret(warehouse.secret_encrypted),
        )
    except Exception:
        return None
    try:
        inner = compiled_sql.strip().rstrip(";")
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM ({inner}) impact_probe_x LIMIT 0"))
            return set(result.keys())
    except Exception:
        return None
    finally:
        engine.dispose()


def _diff_columns(old: set[str] | None, new: set[str] | None) -> tuple[list[str], list[str]]:
    if old is None or new is None:
        return [], []
    return sorted(old - new), sorted(new - old)


def analyze(db: Session, project: MedallionProject) -> ImpactResult:
    """§7.3 — runs "quand des fichiers ont été modifiés depuis la version active". A project
    that's never been built (no active version) or whose workspace hasn't diverged from it at
    all has nothing to analyze — the common case, and the cheapest to short-circuit."""
    binding = project.home_binding
    if not binding.active_version_id:
        return ImpactResult()
    version = db.get(MedallionVersion, binding.active_version_id)
    if version is None or not version.dbt_project_snapshot:
        return ImpactResult()

    old_snapshot = {k: v for k, v in version.dbt_project_snapshot.items() if k != "profiles.yml"}
    current_tree = workspace.export_tree(db, project)
    changed_paths = [
        p for p in current_tree
        if p.endswith(".sql") and (p.startswith("models/silver/") or p.startswith("models/gold/"))
        and old_snapshot.get(p) != current_tree[p]
    ]
    if not changed_paths:
        return ImpactResult()

    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    by_id = {d.id: d for d in datasets}
    files_by_path = {
        pf.path: pf for pf in db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path.in_(changed_paths)).all()
    }

    # One compile pass for the CURRENT tree — its compiled SQL is the SELECT * fallback
    # source for the "after" side (§7.2); a compile failure here just means that fallback is
    # unavailable, never an error for the impact review itself (workspace_sync/dbt_runner
    # already gate the build on parse/compile separately, §7.3's own preconditions).
    compiled = dbt_runner.compile(db, project)
    warehouse = db.get(DataSource, binding.warehouse_source_id)

    result = ImpactResult()
    for path in changed_paths:
        pf = files_by_path.get(path)
        dataset = by_id.get(pf.dataset_id) if pf else None
        if dataset is None:
            continue

        old_cols = _static_output_columns(old_snapshot[path])
        new_cols = _static_output_columns(current_tree[path])
        if new_cols is None and compiled.ok and warehouse is not None:
            new_cols = _live_output_columns(warehouse, compiled.compiled_sql.get(path, ""))
        removed, added = _diff_columns(old_cols, new_cols)

        model_impact = ModelImpact(dataset_id=dataset.id, dataset_name=dataset.name, path=path, columns_removed=removed, columns_added=added)

        if len(removed) == 1 and len(added) == 1:
            model_impact.items.append(ImpactItem("column_renamed", f"« {removed[0]} » semble renommée en « {added[0]} »."))
        elif removed:
            model_impact.items.append(ImpactItem("column_removed", f"colonne(s) supprimée(s) : {', '.join(removed)}."))

        if removed:
            for downstream in datasets:
                if dataset.id in (downstream.upstream_dataset_ids or []):
                    model_impact.items.append(ImpactItem("downstream_model", f"le modèle « {downstream.name} » ({downstream.layer.value}) lit ce modèle en amont."))

            pubs = db.query(SupersetPublication).filter(SupersetPublication.medallion_dataset_id == dataset.id).all()
            if pubs:
                model_impact.items.append(ImpactItem("superset", f"dataset Superset publié (id {pubs[0].superset_dataset_id})."))
            specs = db.query(DashboardSpec).filter(DashboardSpec.medallion_dataset_id == dataset.id).all()
            for spec in specs:
                for ind in spec.indicators or []:
                    used = {c for c in [ind.get("metric_column"), ind.get("time_column")] if c}
                    used.update(ind.get("dimension_columns") or [])
                    hit = used & set(removed)
                    if hit:
                        model_impact.items.append(ImpactItem("superset", f"chart « {ind.get('title') or '?'} » utilise {', '.join(sorted(hit))}."))

            checks = db.query(DataQualityCheck).filter(DataQualityCheck.dataset_id == dataset.id, DataQualityCheck.target_column.in_(removed)).all()
            for c in checks:
                model_impact.items.append(ImpactItem("quality_check", f"contrôle qualité « {c.check_type.value} » porte sur « {c.target_column} »."))

            has_export = db.query(ExportLog.id).filter(ExportLog.dataset_id == dataset.id, ExportLog.kind == ExportKind.csv).first() is not None
            if has_export:
                model_impact.items.append(ImpactItem("export", "ce dataset a déjà été exporté en CSV — sa structure va changer."))

        result.models.append(model_impact)

    return result
