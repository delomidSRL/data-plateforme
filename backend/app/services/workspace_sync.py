"""Module 19 étape 2 §4.3 — `dbt parse` the workspace in a throwaway temp dir, then reconcile
`MedallionDataset` rows from the resulting manifest. This is what keeps the canvas a
*projection* of the code (§0): every workspace write (PUT/POST/DELETE /file) calls sync()
once, over the WHOLE project (never incremental — simplest correct thing, and cheap: `dbt
parse` alone, no compile/run, typically well under a second for a project this size).

Never touches the tenant's own warehouse or Airflow — `dbt parse` needs no live connection at
all, only a profile shaped correctly (see _fake_profiles_yml)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.medallion import DatasetOrigin, MedallionDataset, MedallionLayer, MedallionProject, Materialization, TransformType
from app.models.project_file import ProjectFile
from app.services import dbt_project as dbt_project_service
from app.services import workspace
from app.services.medallion_crud import layer_rank

# 01_unpacked_<name>/02_typed_<name> (§5 rewrite, Module 6 extension) are rendered FROM a
# PayloadStructuration contract, not from any MedallionDataset — a human edit to one of these
# is a real, saved, deployed file (never silently dropped), but it must never spawn a
# duplicate "dataset" of its own just because it happens to sit in models/silver/*.sql.
_STRUCTURATION_FILE_RE = re.compile(r"^models/silver/(01_unpacked|02_typed)_.+\.sql$")
# Module 19 bugfix — NOT anchored to the very start of the file (re.MULTILINE `^` matches any
# line start): a human edit routinely adds a comment or anything else *before* the generated
# `{{ config(...) }}` line (e.g. étape 3's own merge output, which puts the human's addition
# first). Anchoring to position 0 only silently failed to strip it in that case, leaving the
# config wrapper embedded inside `dataset.sql` — which then got wrapped in a SECOND one by
# the very next `_model_sql()` regeneration (doubling on every subsequent edit).
_CONFIG_LINE_RE = re.compile(r"^[ \t]*\{\{\s*config\([^\n]*\)\s*\}\}[ \t]*\n+", re.MULTILINE)
_PATH_IN_MSG_RE = re.compile(r"\(([^()]+\.(?:sql|yml|yaml))\)")
_LINE_IN_MSG_RE = re.compile(r"\bline (\d+)\b")


@dataclass
class SyncError:
    path: str | None
    line: int | None
    column: int | None
    message: str

    def as_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "column": self.column, "message": self.message}


@dataclass
class SyncResult:
    ok: bool
    errors: list[SyncError] = field(default_factory=list)


def dbt_bin() -> str:
    """The control plane's OWN pinned dbt (requirements.txt), sibling of the running
    interpreter — never Airflow's dbt_venv (dag_render.DBT_BIN), a completely different
    container this process has no access to."""
    suffix = ".exe" if os.name == "nt" else ""
    return str(Path(sys.executable).parent / f"dbt{suffix}")


def _fake_profiles_yml(project: MedallionProject) -> str:
    """`dbt parse` never opens a connection — these values are never read for anything but
    shape validation. Real credentials never pass through here."""
    return yaml.safe_dump({
        project.dbt_project_name: {
            "target": "dev",
            "outputs": {"dev": {
                "type": "postgres", "host": "localhost", "port": 5432,
                "user": "parse", "password": "parse", "dbname": "parse",
                "schema": "silver", "threads": 1,
            }},
        },
    }, sort_keys=False)


def ensure_packages_cache(settings) -> Path:
    """Seeds DBT_PACKAGES_CACHE_DIR once — real network call to dbt Hub — the first time any
    project's packages.yml needs dbt-utils/dbt-expectations; every later sync/compile just
    copies from here (§1: no network at parse/compile time once seeded). Pins the exact same
    versions the live build itself pins (dbt_project.DBT_UTILS_VERSION/DBT_EXPECTATIONS_VERSION)."""
    cache_dir = Path(settings.dbt_packages_cache_dir).resolve()
    marker = cache_dir / ".seeded"
    if marker.exists():
        return cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dbtpkgseed_") as tmp_str:
        tmp = Path(tmp_str)
        (tmp / "models").mkdir()
        (tmp / "dbt_project.yml").write_text(yaml.safe_dump({
            "name": "pkg_seed", "version": "1.0.0", "config-version": 2,
            "profile": "pkg_seed", "model-paths": ["models"],
        }), encoding="utf-8")
        (tmp / "packages.yml").write_text(dbt_project_service._packages_yml(), encoding="utf-8")
        (tmp / "profiles.yml").write_text(yaml.safe_dump({
            "pkg_seed": {"target": "dev", "outputs": {"dev": {
                "type": "postgres", "host": "localhost", "port": 5432,
                "user": "x", "password": "x", "dbname": "x", "schema": "public", "threads": 1,
            }}},
        }), encoding="utf-8")
        result = subprocess.run(
            [dbt_bin(), "deps", "--project-dir", str(tmp), "--profiles-dir", str(tmp)],
            capture_output=True, text=True, timeout=settings.dbt_runner_timeout_s,
        )
        if result.returncode != 0 or not (tmp / "dbt_packages").exists():
            raise RuntimeError(f"Impossible d'amorcer le cache de paquets dbt : {result.stdout[-2000:]}\n{result.stderr[-2000:]}")
        for child in (tmp / "dbt_packages").iterdir():
            dest = cache_dir / child.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(child), str(dest))
    marker.write_text("ok", encoding="utf-8")
    return cache_dir


def _extract_errors(log_lines: list[dict]) -> list[SyncError]:
    errors = []
    for j in log_lines:
        info = j.get("info", {})
        if info.get("level") != "error":
            continue
        msg = (j.get("data", {}) or {}).get("exc") or info.get("msg") or "Erreur dbt inconnue."
        path_m = _PATH_IN_MSG_RE.search(msg)
        line_m = _LINE_IN_MSG_RE.search(msg)
        errors.append(SyncError(
            path=path_m.group(1).replace("\\", "/") if path_m else None,
            line=int(line_m.group(1)) if line_m else None,
            column=None, message=msg.strip(),
        ))
    if not errors:
        errors.append(SyncError(path=None, line=None, column=None, message="dbt parse a échoué sans message d'erreur exploitable."))
    return errors


def _run_dbt_parse(tree: dict[str, str], project: MedallionProject, settings) -> tuple[int, list[dict], dict | None]:
    tmpdir = Path(tempfile.mkdtemp(prefix="dbtparse_"))
    try:
        for rel_path, content in tree.items():
            full = tmpdir / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        (tmpdir / "profiles.yml").write_text(_fake_profiles_yml(project), encoding="utf-8")

        if "packages.yml" in tree:
            cache_dir = ensure_packages_cache(settings)
            pkg_dir = tmpdir / "dbt_packages"
            pkg_dir.mkdir(exist_ok=True)
            for child in cache_dir.iterdir():
                if child.name == ".seeded":
                    continue
                shutil.copytree(child, pkg_dir / child.name)

        try:
            result = subprocess.run(
                [dbt_bin(), "parse", "--project-dir", str(tmpdir), "--profiles-dir", str(tmpdir), "--no-use-colors", "--log-format", "json"],
                capture_output=True, text=True, timeout=settings.dbt_runner_timeout_s,
            )
        except subprocess.TimeoutExpired:
            return 1, [{"info": {"level": "error", "msg": f"dbt parse a dépassé le délai imparti ({settings.dbt_runner_timeout_s}s)."}, "data": {}}], None

        log_lines = []
        for line in result.stdout.splitlines():
            try:
                log_lines.append(json.loads(line))
            except ValueError:
                continue

        manifest_path = tmpdir / "target" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
        return result.returncode, log_lines, manifest
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _strip_config_line(content: str) -> str:
    return _CONFIG_LINE_RE.sub("", content, count=1).strip()


def _materialization_from_manifest(value: str | None) -> Materialization:
    try:
        return Materialization(value)
    except (ValueError, TypeError):
        return Materialization.view


def sync(db: Session, project: MedallionProject) -> SyncResult:
    settings = get_settings()
    tree = workspace.export_tree(db, project)
    returncode, log_lines, manifest = _run_dbt_parse(tree, project, settings)
    if returncode != 0 or manifest is None:
        return SyncResult(ok=False, errors=_extract_errors(log_lines))

    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    by_id = {d.id: d for d in datasets}
    model_nodes = {n["name"]: n for n in manifest.get("nodes", {}).values() if n.get("resource_type") == "model"}

    model_files = [
        pf for pf in db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()
        if pf.path.endswith(".sql") and (pf.path.startswith("models/silver/") or pf.path.startswith("models/gold/"))
        and not _STRUCTURATION_FILE_RE.match(pf.path)
    ]

    # Pass 1 — every eligible file ends up linked to exactly one dataset, named after itself.
    # New MedallionDataset rows are flushed immediately so pass 2 can resolve depends_on
    # against a complete, stable {dbt_model_name: dataset.id} map.
    for pf in model_files:
        stem = Path(pf.path).stem
        layer = MedallionLayer.silver if pf.path.startswith("models/silver/") else MedallionLayer.gold
        if pf.dataset_id and pf.dataset_id in by_id:
            ds = by_id[pf.dataset_id]
            ds.dbt_model_name = stem
            # A `visual` dataset's own "Nom" is a separate, deliberately-chosen field (the
            # declarative form's own "Nom" vs "Nom du modèle dbt") — a rename through the
            # Code tab must not silently overwrite it. A `code` dataset never had such a
            # choice made (§ Pass 1 below always set name=dbt_model_name=stem at creation),
            # so its display name keeps following the file.
            if ds.origin == DatasetOrigin.code:
                ds.name = stem
        else:
            node = model_nodes.get(stem)
            ds = MedallionDataset(
                project_id=project.id, layer=layer, name=stem, dbt_model_name=stem,
                materialization=_materialization_from_manifest((node or {}).get("config", {}).get("materialized")),
                sql=_strip_config_line(pf.content), transform_type=TransformType.dbt, origin=DatasetOrigin.code,
            )
            db.add(ds)
            db.flush()
            pf.dataset_id = ds.id
            by_id[ds.id] = ds
    db.flush()

    dataset_by_name = {d.dbt_model_name: d.id for d in by_id.values() if d.layer != MedallionLayer.bronze and d.transform_type == TransformType.dbt}
    dataset_by_bronze_name = {d.name: d.id for d in by_id.values() if d.layer == MedallionLayer.bronze}
    dataset_by_ml_output = {(d.output_table or d.name): d.id for d in by_id.values() if d.layer == MedallionLayer.gold and d.transform_type == TransformType.python}

    def resolve(node_id: str) -> int | None:
        parts = node_id.split(".")
        if parts[0] == "model":
            return dataset_by_name.get(parts[-1])
        if parts[0] == "source":
            source_name, table = parts[-2], parts[-1]
            if source_name == "bronze":
                return dataset_by_bronze_name.get(table)
            if source_name == "gold_ml":
                return dataset_by_ml_output.get(table)
        return None  # unmapped ref (e.g. a structuration stage) — not tracked, not an error

    # Pass 2 — sql/materialization/description/upstream_dataset_ids, and the same
    # equal-or-lower-layer rule the canvas's own CRUD enforces (medallion_crud.layer_rank).
    lineage_errors: list[SyncError] = []
    for pf in model_files:
        ds = by_id.get(pf.dataset_id)
        if ds is None:
            continue
        node = model_nodes.get(ds.dbt_model_name)
        if node is None:
            continue
        ds.materialization = _materialization_from_manifest(node.get("config", {}).get("materialized"))
        ds.description = node.get("description") or None
        ds.sql = _strip_config_line(pf.content)
        upstream_ids = sorted({uid for dep in node.get("depends_on", {}).get("nodes", []) if (uid := resolve(dep)) is not None})
        for uid in upstream_ids:
            up = by_id.get(uid)
            if up is not None and layer_rank(up.layer) > layer_rank(ds.layer):
                lineage_errors.append(SyncError(
                    path=pf.path, line=None, column=None,
                    message=f"« {ds.dbt_model_name} » ({ds.layer.value}) référence « {up.name} » ({up.layer.value}), une couche supérieure — référence interdite.",
                ))
        ds.upstream_dataset_ids = upstream_ids

    if lineage_errors:
        return SyncResult(ok=False, errors=lineage_errors)
    return SyncResult(ok=True)
