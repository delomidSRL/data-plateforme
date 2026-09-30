"""Module 19 étape 1 — the project's persisted dbt workspace (`project_files`). This is the
ONLY writer in this stage: no human-edit endpoint exists yet (étape 2), so a file's `content`
and `base_content` always move together here — `materialize()` simply mirrors whatever the
existing generators (`dbt_project.generate_project_files`, and through it `gold_builder` /
`dbt_test_renderer`) already produce, exactly as before, just persisted instead of thrown away.

`export_tree()` is what the build now deploys (medallion_deploy.build_project), always
byte-identical to what `dbt_project.generate_project_files` itself just returned, since
`materialize()` runs first on every build and is idempotent."""
import hashlib
import logging

from sqlalchemy.orm import Session

from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, TransformType
from app.models.medallion import MedallionVersion
from app.models.payload_structuration import PayloadStructuration
from app.models.project_file import ProjectFile
from app.services import dbt_project

logger = logging.getLogger("app.services.workspace")


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _dataset_path_map(datasets: list[MedallionDataset]) -> dict[str, int]:
    """models/{silver|gold}/{dbt_model_name}.sql -> dataset.id — the only files with a 1:1
    dataset (bronze has no per-dataset model file, its ingestion is Python not dbt; a gold
    python/ML node is not a dbt model either)."""
    mapping: dict[str, int] = {}
    for ds in datasets:
        if ds.layer == MedallionLayer.bronze:
            continue
        if ds.layer == MedallionLayer.gold and ds.transform_type == TransformType.python:
            continue
        mapping[f"models/{ds.layer.value}/{ds.dbt_model_name}.sql"] = ds.id
    return mapping


def materialize(db: Session, project: MedallionProject, files: dict[str, str], datasets: list[MedallionDataset] | None = None, generator: str = "dbt_project") -> None:
    """Writes `files` (a generator's output — NEVER pass `profiles.yml`, §2) into the
    workspace as the new base. Idempotent: a file whose content is unchanged does not bump
    `version` (§3.3). A file no longer produced by the generator (dataset renamed/removed) is
    dropped — safe in étape 1 only, since every row here is generator-owned; étape 3's
    workspace_merge is what stops doing that once human-authored files exist."""
    if "profiles.yml" in files:
        files = {path: content for path, content in files.items() if path != "profiles.yml"}
    path_to_dataset = _dataset_path_map(datasets or [])

    existing = {pf.path: pf for pf in db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()}
    seen: set[str] = set()

    for path, content in files.items():
        seen.add(path)
        h = hash_content(content)
        dataset_id = path_to_dataset.get(path)
        row = existing.get(path)
        if row is None:
            db.add(ProjectFile(
                project_id=project.id, path=path, content=content, content_hash=h,
                base_content=content, base_hash=h, generator=generator, dataset_id=dataset_id, version=1,
            ))
        else:
            if row.content_hash != h:
                row.content = content
                row.content_hash = h
                row.base_content = content
                row.base_hash = h
                row.generator = generator
                row.version += 1
            if row.dataset_id != dataset_id:
                row.dataset_id = dataset_id

    for path, row in existing.items():
        if path not in seen:
            db.delete(row)

    db.flush()


def export_tree(db: Session, project: MedallionProject) -> dict[str, str]:
    rows = db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()
    return {row.path: row.content for row in rows}


def has_workspace(db: Session, project: MedallionProject) -> bool:
    return db.query(ProjectFile.id).filter(ProjectFile.project_id == project.id).first() is not None


def bootstrap_files(db: Session, project: MedallionProject, datasets: list[MedallionDataset]) -> dict[str, str]:
    """What the lazy, read-side backfill (§3.4 — "premier passage / migration", done on first
    touch rather than as an Alembic data migration, see workspace_sync module docstring below)
    materializes from when nobody has ever built this project through this module: the
    for_export rendering needs no warehouse secret (its own profiles.yml is never produced,
    §8 of the export module) — exactly what a read-only tree/Monaco view needs, and strictly
    equivalent to a live build's own output once profiles.yml is stripped from both."""
    bronze_ids = [d.id for d in datasets if d.layer == MedallionLayer.bronze]
    structurations = (
        {s.dataset_id: s for s in db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id.in_(bronze_ids)).all()}
        if bronze_ids else {}
    )
    return dbt_project.generate_project_files(db, project, datasets, warehouse=None, structurations=structurations, for_export=True)


def materialize_if_empty(db: Session, project: MedallionProject, datasets: list[MedallionDataset]) -> bool:
    """Module 19 §3.4's backfill, triggered lazily on first touch (a build, or the Code tab's
    first open) instead of running as an Alembic data migration: producing a project's dbt
    tree needs live service context (DbtMacro / dq_flag_registry queries, a real DB session)
    that this codebase's migrations never carry — every existing Alembic backfill here is
    plain SQL (see f3a8c1d92b47_add_environment_bindings.py), and importing service code into
    a migration would let one mis-configured project abort `alembic upgrade head` for the
    whole platform. Returns True if this call actually materialized."""
    if has_workspace(db, project):
        return False
    materialize(db, project, bootstrap_files(db, project, datasets), datasets=datasets)
    _log_active_version_drift(db, project)
    return True


def _log_active_version_drift(db: Session, project: MedallionProject) -> None:
    """§3.4 — "tout écart est journalisé" : never blocks, never raises: a mismatch would mean
    dbt_project.generate_project_files is non-deterministic (a real bug to fix), not something
    the backfill itself should paper over."""
    try:
        binding = project.home_binding
    except RuntimeError:
        return
    if not binding.active_version_id:
        return
    version = db.get(MedallionVersion, binding.active_version_id)
    if version is None or not version.dbt_project_snapshot:
        return
    snapshot = {k: v for k, v in version.dbt_project_snapshot.items() if k != "profiles.yml"}
    current = export_tree(db, project)
    missing = sorted(set(snapshot) - set(current))
    extra = sorted(set(current) - set(snapshot))
    changed = sorted(p for p in (set(snapshot) & set(current)) if snapshot[p] != current[p])
    if missing or extra or changed:
        logger.warning(
            "workspace backfill for project %s (%r) diverges from active version %s snapshot — "
            "missing=%s extra=%s changed=%s (check dbt_project.generate_project_files for non-determinism)",
            project.id, project.name, binding.active_version_id, missing, extra, changed,
        )
