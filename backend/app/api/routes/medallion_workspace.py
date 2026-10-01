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
from app.models.project_file_conflict import ConflictStatus, ProjectFileConflict
from app.models.user import User
from app.schemas.medallion import (
    CompileOut,
    CompileRequest,
    ConflictActionOut,
    ConflictOut,
    ConflictResolveRequest,
    FileAuditEntryOut,
    ImpactItemOut,
    ImpactOut,
    ModelImpactOut,
    RunDevNodeOut,
    RunDevOut,
    RunDevRequest,
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
from app.services import dbt_runner, impact, jinja_guard, workspace, workspace_merge, workspace_sync

router = APIRouter(prefix="/api/medallion/projects", tags=["medallion-workspace"])

_ALLOWED_EXTENSIONS = (".sql", ".yml", ".yaml", ".md", ".csv")
# §4.5 — generator-managed, never a direct human write: packages.yml (M16 ext, pinned
# versions); profiles.yml never even exists in the workspace (§2), listed here too so a
# would-be write gets the same clear "géré" message instead of a confusing 404.
_MANAGED_FILES = {"packages.yml", "profiles.yml"}
# A folder has no identity of its own here — only files do (§ the whole ProjectFile model) —
# so an otherwise-empty one can only be represented by a placeholder inside it, same convention
# as git's own .gitkeep. Empty, never shown as a real file by the Code tab's tree (CodeTab.jsx's
# buildTree), and never picked up as a dbt model (workspace_sync only looks at *.sql).
FOLDER_MARKER = ".gitkeep"


def _validate_path(path: str) -> None:
    if not path or path.startswith("/") or path.endswith("/") or "\\" in path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chemin de fichier invalide.")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chemin de fichier invalide.")
    if not path.endswith(_ALLOWED_EXTENSIONS) and parts[-1] != FOLDER_MARKER:
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


@router.get("/{pid}/workspace/modified-files", response_model=list[str])
def get_modified_files(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 19 §5.4 — what a restore's confirmation dialog warns about before redeploying:
    every file restoring would silently discard (restoring ANY version wholesale-replaces the
    whole workspace, so this list doesn't depend on which version is being restored)."""
    _ensure_workspace(db, project)
    return workspace.modified_paths_overwritten_by(db, project)


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


# ---------------- Conflicts — "À arbitrer" (étape 3) ----------------

def _get_conflict(db: Session, project: MedallionProject, cid: int) -> ProjectFileConflict:
    conflict = db.query(ProjectFileConflict).filter(ProjectFileConflict.id == cid, ProjectFileConflict.project_id == project.id).first()
    if conflict is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conflit introuvable.")
    return conflict


def _conflict_action_out(db: Session, project: MedallionProject, conflict: ProjectFileConflict, row: ProjectFile) -> ConflictActionOut:
    sync_result = _sync_and_settle(db, project)
    db.refresh(conflict)
    db.refresh(row)
    return ConflictActionOut(conflict=ConflictOut.model_validate(conflict), file=_file_out(row), sync=WorkspaceSyncOut(ok=sync_result.ok, errors=[SyncErrorOut(**e.as_dict()) for e in sync_result.errors]))


@router.get("/{pid}/workspace/conflicts", response_model=list[ConflictOut])
def list_conflicts(status_filter: str | None = None, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    q = db.query(ProjectFileConflict).filter(ProjectFileConflict.project_id == project.id)
    if status_filter:
        try:
            q = q.filter(ProjectFileConflict.status == ConflictStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Statut de conflit invalide.")
    rows = q.order_by(ProjectFileConflict.created_at.desc()).all()
    return [ConflictOut.model_validate(c) for c in rows]


@router.get("/{pid}/workspace/conflicts/{cid}", response_model=ConflictOut)
def get_conflict(cid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    return ConflictOut.model_validate(_get_conflict(db, project, cid))


@router.post("/{pid}/workspace/conflicts/{cid}/accept", response_model=ConflictActionOut)
def accept_conflict(cid: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project)):
    conflict = _get_conflict(db, project, cid)
    if conflict.status != ConflictStatus.proposed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Seule une proposition de fusion propre peut être acceptée telle quelle — utilisez la résolution pour un conflit ouvert.")
    row = workspace_merge.accept_conflict(db, project, conflict, current_user)
    db.add(ProjectFileAudit(project_id=project.id, path=row.path, action=ProjectFileAuditAction.accept_merge, actor_id=current_user.id, content_hash_before=row.content_hash, content_hash_after=row.content_hash))
    db.commit()
    return _conflict_action_out(db, project, conflict, row)


@router.post("/{pid}/workspace/conflicts/{cid}/resolve", response_model=ConflictActionOut)
def resolve_conflict_endpoint(cid: int, payload: ConflictResolveRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project)):
    conflict = _get_conflict(db, project, cid)
    if conflict.status not in (ConflictStatus.proposed, ConflictStatus.open):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce conflit a déjà été traité.")
    _validate_size(payload.content)
    _check_guard(conflict.path, payload.content)
    before_hash = workspace.hash_content(conflict.ours_content)
    row = workspace_merge.resolve_conflict(db, project, conflict, payload.content, current_user)
    db.add(ProjectFileAudit(project_id=project.id, path=row.path, action=ProjectFileAuditAction.resolve, actor_id=current_user.id, content_hash_before=before_hash, content_hash_after=row.content_hash))
    db.commit()
    return _conflict_action_out(db, project, conflict, row)


@router.post("/{pid}/workspace/conflicts/{cid}/discard", response_model=ConflictActionOut)
def discard_conflict_endpoint(cid: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project)):
    conflict = _get_conflict(db, project, cid)
    if conflict.status not in (ConflictStatus.proposed, ConflictStatus.open):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce conflit a déjà été traité.")
    row = workspace_merge.discard_conflict(db, project, conflict, current_user)
    db.add(ProjectFileAudit(project_id=project.id, path=row.path, action=ProjectFileAuditAction.discard, actor_id=current_user.id, content_hash_before=row.content_hash, content_hash_after=row.content_hash))
    db.commit()
    return _conflict_action_out(db, project, conflict, row)


# ---------------- Compile & run dev (étape 4) ----------------

def _default_compile_select(db: Session, project: MedallionProject) -> str | None:
    """§6.4 — no explicit `select` compiles "les fichiers modifiés depuis le dernier build".
    Nothing modified -> None (no --select at all, compiles the whole project) rather than an
    empty, meaningless selection."""
    rows = db.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.dataset_id.isnot(None)).all()
    modified_dataset_ids = [r.dataset_id for r in rows if workspace.is_modified(r)]
    if not modified_dataset_ids:
        return None
    names = [
        d.dbt_model_name for d in db.query(MedallionDataset).filter(MedallionDataset.id.in_(modified_dataset_ids)).all()
        if d.dbt_model_name
    ]
    return " ".join(names) if names else None


@router.post("/{pid}/workspace/compile", response_model=CompileOut)
def post_compile(payload: CompileRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """§6.4 — read-only (no side effect), available to a non-owner too, unlike run-dev."""
    select = payload.select if payload.select is not None else _default_compile_select(db, project)
    result = dbt_runner.compile(db, project, select=select)
    return CompileOut(ok=result.ok, compiled_sql=result.compiled_sql, errors=[SyncErrorOut(**e.as_dict()) for e in result.errors])


@router.post("/{pid}/workspace/run-dev", response_model=RunDevOut)
def post_run_dev(payload: RunDevRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """§6.4 — owner-only (unlike compile): this one actually materializes on the dev warehouse."""
    try:
        result = dbt_runner.run_dev(db, project, select=payload.select)
    except dbt_runner.RunnerBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except dbt_runner.DbtRunnerError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return RunDevOut(
        ok=result.ok,
        nodes=[RunDevNodeOut(unique_id=n.unique_id, name=n.name, resource_type=n.resource_type, status=n.status, execution_time=n.execution_time) for n in result.nodes],
        errors=[SyncErrorOut(**e.as_dict()) for e in result.errors],
    )


# ---------------- Impact & publication (étape 5) ----------------

@router.get("/{pid}/workspace/impact", response_model=ImpactOut)
def get_impact(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """§7.2/§7.3 — the same review the build itself runs (and blocks on, once, until
    confirmed) — exposed read-only so the "Build & déployer" flow can show it BEFORE the
    build call, instead of the human discovering it only from a 409."""
    result = impact.analyze(db, project)
    return ImpactOut(
        has_impact=result.has_impact,
        models=[
            ModelImpactOut(
                dataset_id=m.dataset_id, dataset_name=m.dataset_name, path=m.path,
                columns_removed=m.columns_removed, columns_added=m.columns_added,
                items=[ImpactItemOut(severity=i.severity, message=i.message) for i in m.items],
            )
            for m in result.models
        ],
    )


@router.get("/{pid}/workspace/file/audit", response_model=list[FileAuditEntryOut])
def get_file_audit(path: str, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """§7.4 — "qui (ou quel générateur) a modifié [ce fichier] et quand" ; jamais de contenu,
    seulement des hash (déjà vrai à l'écriture, §7.4 du modèle ProjectFileAudit)."""
    rows = (
        db.query(ProjectFileAudit)
        .filter(ProjectFileAudit.project_id == project.id, ProjectFileAudit.path == path)
        .order_by(ProjectFileAudit.created_at.desc())
        .all()
    )
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    names = dict(db.query(User.id, User.name).filter(User.id.in_(actor_ids)).all()) if actor_ids else {}
    return [
        FileAuditEntryOut(
            id=r.id, action=r.action.value, actor_name=names.get(r.actor_id), generator=r.generator,
            content_hash_before=r.content_hash_before, content_hash_after=r.content_hash_after, created_at=r.created_at,
        )
        for r in rows
    ]
