"""Module 19 — the project's persisted dbt workspace (`project_files`). `export_tree()` is
what the build deploys (medallion_deploy.build_project), and read-side helpers here back the
Code tab (étape 1) and `code_modified` (étape 2).

`materialize()` is the original, pre-merge writer (étape 1): it still mirrors a generator's
output 1:1 when nothing is modified (the common case — new file, or an intact one), but for a
MODIFIED file it only slides the base forward, never proposing anything. It's still what the
lazy bootstrap (`materialize_if_empty`) uses, since a brand-new workspace can never have a
modified file to merge. Every OTHER call site (the actual build) goes through
`services/workspace_merge.apply_generated()` instead (étape 3) — the merge-aware superset of
this same per-file logic, which turns that "slide the base" case into an actual three-way
merge proposal or conflict."""
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, TransformType
from app.models.medallion import MedallionVersion
from app.models.payload_structuration import PayloadStructuration
from app.models.project_file import ProjectFile
from app.models.project_file_conflict import ConflictStatus, ProjectFileConflict
from app.services import dbt_project

logger = logging.getLogger("app.services.workspace")


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def dataset_path_map(datasets: list[MedallionDataset]) -> dict[str, int]:
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


def is_modified(row: ProjectFile) -> bool:
    """A file with no base at all (`base_hash is None`, a human-created file that's never
    been through a generator pass) counts as modified too — there is nothing "intact" about
    it to silently update. Shared by materialize() and the workspace status endpoints."""
    return row.base_hash is None or row.content_hash != row.base_hash


def materialize(db: Session, project: MedallionProject, files: dict[str, str], datasets: list[MedallionDataset] | None = None, generator: str = "dbt_project") -> None:
    """Writes `files` (a generator's output — NEVER pass `profiles.yml`, §2) into the
    workspace as the new base. Idempotent: a file whose content is unchanged does not bump
    `version` (§3.3).

    Module 19 étape 2 — the non-negotiable invariant from §0: "la plateforme ne réécrit
    jamais silencieusement un fichier modifié par un humain". A file that has diverged from
    its base (`is_modified`) never has its `content` touched here, no matter what the
    generator would now produce — only `base_content`/`base_hash` slide forward, so the file
    keeps reading as "modifié" against an up-to-date baseline until étape 3's workspace_merge
    exists to actually reconcile the two. Same for deletion: a file the generator no longer
    produces (dataset renamed/removed) is only dropped here while it was still intact —
    dropping a human-modified file without a merge conversation would be exactly the
    silent-overwrite this invariant forbids."""
    if "profiles.yml" in files:
        files = {path: content for path, content in files.items() if path != "profiles.yml"}
    path_to_dataset = dataset_path_map(datasets or [])

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
        elif not is_modified(row):
            if row.content_hash != h:
                row.content = content
                row.content_hash = h
                row.base_content = content
                row.base_hash = h
                row.generator = generator
                row.version += 1
            if row.dataset_id != dataset_id:
                row.dataset_id = dataset_id
        else:
            # Human-modified: `content`/`version` are theirs, never moved here. Only the
            # base baseline advances, so a later étape-3 merge diffs against what the
            # generator would produce TODAY, not against whatever it produced originally.
            if row.base_hash != h:
                row.base_content = content
                row.base_hash = h
                row.generator = generator
            if row.dataset_id != dataset_id:
                row.dataset_id = dataset_id

    for path, row in existing.items():
        if path not in seen and not is_modified(row):
            db.delete(row)

    db.flush()


def modified_paths_overwritten_by(db: Session, project: MedallionProject) -> list[str]:
    """Module 19 §5.4 — what a restore's confirmation dialog warns about: every currently
    `modified`/`code` file, which `replace_all()` is about to either plainly overwrite (if
    the target snapshot still has that path) or drop outright (if it doesn't) — either way,
    the human's own edit is gone. No merge is offered here: restoring is already the
    explicit, user-confirmed act M8 treats as a deliberate replacement."""
    rows = db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()
    return sorted(row.path for row in rows if is_modified(row))


def replace_all(db: Session, project: MedallionProject, files: dict[str, str], datasets: list[MedallionDataset] | None = None, generator: str = "restore") -> None:
    """Module 19 §5.4 — a restore's own writer: a plain, unconditional overwrite (content AND
    base both become `files`), never a merge — restoring is already the explicit, confirmed
    act the spec treats as a deliberate replacement, not a regeneration to reconcile against.
    Any workspace path NOT in `files` is dropped outright (the workspace becomes exactly the
    snapshot, matching "remplace l'espace de travail"). Active conflicts on any touched path
    are discarded — their underlying file no longer has the base/content they were about."""
    if "profiles.yml" in files:
        files = {path: content for path, content in files.items() if path != "profiles.yml"}
    path_to_dataset = dataset_path_map(datasets or [])
    existing = {pf.path: pf for pf in db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()}

    for path, content in files.items():
        h = hash_content(content)
        dataset_id = path_to_dataset.get(path)
        row = existing.get(path)
        if row is None:
            db.add(ProjectFile(
                project_id=project.id, path=path, content=content, content_hash=h,
                base_content=content, base_hash=h, generator=generator, dataset_id=dataset_id, version=1,
            ))
        else:
            row.content = content
            row.content_hash = h
            row.base_content = content
            row.base_hash = h
            row.generator = generator
            row.dataset_id = dataset_id
            row.version += 1

    for path, row in existing.items():
        if path not in files:
            db.delete(row)

    # Every active proposal/conflict is now stale — the file it was about just got wholesale
    # replaced, so accepting/resolving it later would apply a merge against content that no
    # longer exists. `discarded`, not deleted: still visible in a conflict's own history.
    stale = db.query(ProjectFileConflict).filter(
        ProjectFileConflict.project_id == project.id,
        ProjectFileConflict.status.in_([ConflictStatus.proposed, ConflictStatus.open]),
    ).all()
    for c in stale:
        c.status = ConflictStatus.discarded
        c.resolved_at = datetime.now(timezone.utc)

    db.flush()


def export_tree(db: Session, project: MedallionProject) -> dict[str, str]:
    rows = db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()
    return {row.path: row.content for row in rows}


def bulk_code_modified(db: Session, dataset_ids: list[int]) -> dict[int, bool]:
    """Module 19 §4.4 — `MedallionDataset.code_modified` (computed, never stored, same
    pattern as payload_structure.bulk_payload_backed): true once a dataset's linked file
    diverges from its base (or never had one, `origin=code`). Datasets with no linked file
    at all (bronze, python/ML nodes) are simply absent from the result — callers default
    to False."""
    if not dataset_ids:
        return {}
    rows = db.query(ProjectFile).filter(ProjectFile.dataset_id.in_(dataset_ids)).all()
    return {row.dataset_id: is_modified(row) for row in rows}


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
