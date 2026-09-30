"""Module 19 — the project's dbt workspace (the "Code" tab): read explorer (étape 1) + human
edits, synced back onto the canvas (étape 2). Split out of routes/medallion.py, which was
already large before this — same `/api/medallion/projects` prefix, mounted as its own router
(same pattern as medallion_admin.py's `/api/medallion/admin`)."""
import difflib

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.deps_medallion import get_owned_project, get_readable_project
from app.core.config import get_settings
from app.db.session import get_db
from app.models.medallion import MedallionDataset, MedallionProject, MedallionVersion, WorkspaceParseStatus
from app.models.project_file import ProjectFile
from app.models.project_file_audit import ProjectFileAudit, ProjectFileAuditAction
from app.models.user import User
from app.schemas.medallion import (
    SyncErrorOut,
    WorkspaceFileContentOut,
    WorkspaceFileCreate,
    WorkspaceFileDelete,
    WorkspaceFileDiffOut,
    WorkspaceFileMove,
    WorkspaceFileOut,
    WorkspaceFileWrite,
    WorkspaceSyncOut,
    WorkspaceTreeOut,
    WorkspaceWriteOut,
)
from app.services import jinja_guard, workspace, workspace_sync

router = APIRouter(prefix="/api/medallion/projects", tags=["medallion-workspace"])

_ALLOWED_EXTENSIONS = (".sql", ".yml", ".yaml", ".md", ".csv")
# §4.5 — generator-managed, never a direct human write: packages.yml (M16 ext, pinned
# versions); profiles.yml never even exists in the workspace (§2), listed here too so a
# would-be write gets the same clear "géré" message instead of a confusing 404.
_MANAGED_FILES = {"packages.yml", "profiles.yml"}


def _validate_path(path: str) -> None:
    if not path or path.startswith("/") or path.endswith("/") or "\\" in path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chemin de fichier invalide.")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chemin de fichier invalide.")
    if not path.endswith(_ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Extension non autorisée — extensions acceptées : {', '.join(_ALLOWED_EXTENSIONS)}.")
    if path in _MANAGED_FILES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"« {path} » est géré par la plateforme et n'est pas modifiable ici.")


def _validate_size(content: str) -> None:
    max_bytes = get_settings().workspace_max_file_bytes
    if len(content.encode("utf-8")) > max_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Fichier trop volumineux (max {max_bytes // 1024} Ko).")


def _check_guard(path: str, content: str) -> None:
    violations = jinja_guard.check_source(path, content)
    if violations:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"line": v.line, "message": v.message} for v in violations],
        )


def _workspace_file_status(pf: ProjectFile) -> str:
    if pf.base_hash is None:
        return "code"
    if pf.content_hash != pf.base_hash:
        return "modified"
    return "generated"


def _file_out(row: ProjectFile) -> WorkspaceFileContentOut:
    return WorkspaceFileContentOut(
        path=row.path, content=row.content, status=_workspace_file_status(row),
        dataset_id=row.dataset_id, version=row.version, generator=row.generator, updated_at=row.updated_at,
    )


def _ensure_workspace(db: Session, project: MedallionProject) -> None:
    """§3.4's backfill, done lazily on first touch of the Code tab rather than as an Alembic
    data migration (see workspace.materialize_if_empty's own docstring for why) — commits only
    when it actually had to materialize something, so an already-backfilled project's GET stays
    a plain read."""
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    if workspace.materialize_if_empty(db, project, datasets):
        db.commit()


def _sync_and_settle(db: Session, project: MedallionProject) -> workspace_sync.SyncResult:
    """Runs sync() as its own independently-committed step, AFTER whatever file write the
    caller already committed — so a sync/lineage failure only discards sync()'s own
    just-flushed dataset mutations, never the human's already-saved file content (§4.2 point
    4: "le fichier EST enregistré [...] mais le projet passe en état « parse en erreur »")."""
    result = workspace_sync.sync(db, project)
    if result.ok:
        db.commit()
    else:
        db.rollback()
    project.workspace_parse_status = WorkspaceParseStatus.ok if result.ok else WorkspaceParseStatus.error
    project.workspace_parse_errors = None if result.ok else [e.as_dict() for e in result.errors]
    db.commit()
    return result


def _write_out(row: ProjectFile, sync_result: workspace_sync.SyncResult) -> WorkspaceWriteOut:
    return WorkspaceWriteOut(
        path=row.path, content=row.content, status=_workspace_file_status(row),
        dataset_id=row.dataset_id, version=row.version, generator=row.generator, updated_at=row.updated_at,
        sync=WorkspaceSyncOut(ok=sync_result.ok, errors=[SyncErrorOut(**e.as_dict()) for e in sync_result.errors]),
    )


# ---------------- Read (étape 1) ----------------

@router.get("/{pid}/workspace/tree", response_model=WorkspaceTreeOut)
def get_workspace_tree(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    _ensure_workspace(db, project)
    rows = db.query(ProjectFile).filter(ProjectFile.project_id == project.id).order_by(ProjectFile.path).all()
    return WorkspaceTreeOut(files=[
        WorkspaceFileOut(path=r.path, status=_workspace_file_status(r), dataset_id=r.dataset_id, version=r.version, generator=r.generator, updated_at=r.updated_at)
        for r in rows
    ])


@router.get("/{pid}/workspace/file", response_model=WorkspaceFileContentOut)
def get_workspace_file(path: str, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    _ensure_workspace(db, project)
    row = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path == path).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fichier introuvable dans l'espace de travail.")
    return _file_out(row)


@router.get("/{pid}/workspace/file/diff", response_model=WorkspaceFileDiffOut)
def get_workspace_file_diff(path: str, against: str = "base", db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    if against not in ("base", "active_version"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="`against` doit être « base » ou « active_version ».")
    _ensure_workspace(db, project)
    row = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path == path).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fichier introuvable dans l'espace de travail.")

    if against == "base":
        other_content = row.base_content or ""
        other_label = f"{path} (base)"
    else:
        binding = project.home_binding
        version = db.get(MedallionVersion, binding.active_version_id) if binding.active_version_id else None
        snapshot = (version.dbt_project_snapshot or {}) if version else {}
        other_content = snapshot.get(path, "")
        other_label = f"{path} (version active)"

    diff_lines = difflib.unified_diff(
        other_content.splitlines(keepends=True), row.content.splitlines(keepends=True),
        fromfile=other_label, tofile=f"{path} (actuel)",
    )
    return WorkspaceFileDiffOut(path=path, against=against, diff="".join(diff_lines))


# ---------------- Write (étape 2) ----------------

@router.put("/{pid}/workspace/file", response_model=WorkspaceWriteOut)
def put_workspace_file(
    payload: WorkspaceFileWrite, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project),
):
    _validate_path(payload.path)
    _validate_size(payload.content)
    _check_guard(payload.path, payload.content)

    row = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path == payload.path).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fichier introuvable — utilisez la création pour un nouveau fichier.")
    if row.version != payload.if_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"message": "Ce fichier a été modifié entretemps.", "current": _file_out(row).model_dump(mode="json")})

    before_hash = row.content_hash
    row.content = payload.content
    row.content_hash = workspace.hash_content(payload.content)
    row.version += 1
    row.updated_by = current_user.id
    project.has_pending_changes = True
    db.add(ProjectFileAudit(
        project_id=project.id, path=row.path, action=ProjectFileAuditAction.update,
        actor_id=current_user.id, content_hash_before=before_hash, content_hash_after=row.content_hash,
    ))
    db.commit()
    db.refresh(row)

    sync_result = _sync_and_settle(db, project)
    db.refresh(row)
    return _write_out(row, sync_result)


@router.post("/{pid}/workspace/file", response_model=WorkspaceWriteOut, status_code=status.HTTP_201_CREATED)
def post_workspace_file(
    payload: WorkspaceFileCreate, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project),
):
    _validate_path(payload.path)
    _validate_size(payload.content)
    _check_guard(payload.path, payload.content)

    if db.query(ProjectFile.id).filter(ProjectFile.project_id == project.id, ProjectFile.path == payload.path).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un fichier existe déjà à cet emplacement.")

    row = ProjectFile(
        project_id=project.id, path=payload.path, content=payload.content, content_hash=workspace.hash_content(payload.content),
        base_content=None, base_hash=None, generator=None, dataset_id=None, version=1, updated_by=current_user.id,
    )
    db.add(row)
    project.has_pending_changes = True
    db.flush()
    db.add(ProjectFileAudit(
        project_id=project.id, path=row.path, action=ProjectFileAuditAction.create,
        actor_id=current_user.id, content_hash_before=None, content_hash_after=row.content_hash,
    ))
    db.commit()
    db.refresh(row)

    sync_result = _sync_and_settle(db, project)
    db.refresh(row)
    return _write_out(row, sync_result)


@router.post("/{pid}/workspace/file/move", response_model=WorkspaceWriteOut)
def move_workspace_file(
    payload: WorkspaceFileMove, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project),
):
    if payload.from_path in _MANAGED_FILES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"« {payload.from_path} » est géré par la plateforme et n'est pas modifiable ici.")
    _validate_path(payload.to_path)

    row = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path == payload.from_path).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fichier introuvable.")
    if row.version != payload.if_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"message": "Ce fichier a été modifié entretemps.", "current": _file_out(row).model_dump(mode="json")})
    if db.query(ProjectFile.id).filter(ProjectFile.project_id == project.id, ProjectFile.path == payload.to_path).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un fichier existe déjà à cet emplacement.")

    before_hash = row.content_hash
    old_path = row.path
    row.path = payload.to_path
    row.version += 1
    row.updated_by = current_user.id
    project.has_pending_changes = True
    db.add(ProjectFileAudit(
        project_id=project.id, path=f"{old_path} -> {payload.to_path}", action=ProjectFileAuditAction.move,
        actor_id=current_user.id, content_hash_before=before_hash, content_hash_after=row.content_hash,
    ))
    db.commit()
    db.refresh(row)

    sync_result = _sync_and_settle(db, project)
    db.refresh(row)
    return _write_out(row, sync_result)


@router.delete("/{pid}/workspace/file", response_model=WorkspaceSyncOut)
def delete_workspace_file(
    payload: WorkspaceFileDelete, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project),
):
    if payload.path in _MANAGED_FILES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"« {payload.path} » est géré par la plateforme et n'est pas modifiable ici.")

    row = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.path == payload.path).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fichier introuvable.")
    if row.version != payload.if_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"message": "Ce fichier a été modifié entretemps.", "current": _file_out(row).model_dump(mode="json")})

    # §4.3 — deleting a file linked to a dataset deletes that dataset too (M3 CRUD), but only
    # once the caller explicitly confirms (§12 décision 6) — never silently.
    linked_dataset = db.get(MedallionDataset, row.dataset_id) if row.dataset_id else None
    if linked_dataset is not None and not payload.confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "message": f"Ce fichier est lié au dataset « {linked_dataset.name} » — confirmez pour le supprimer aussi.",
            "dataset_id": linked_dataset.id, "dataset_name": linked_dataset.name,
        })

    db.add(ProjectFileAudit(
        project_id=project.id, path=row.path, action=ProjectFileAuditAction.delete,
        actor_id=current_user.id, content_hash_before=row.content_hash, content_hash_after=None,
    ))
    if linked_dataset is not None:
        db.delete(linked_dataset)
    db.delete(row)
    project.has_pending_changes = True
    db.commit()

    sync_result = _sync_and_settle(db, project)
    return WorkspaceSyncOut(ok=sync_result.ok, errors=[SyncErrorOut(**e.as_dict()) for e in sync_result.errors])
