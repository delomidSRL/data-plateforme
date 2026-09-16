from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.medallion import MedallionFolder, MedallionProject
from app.models.project_environment_binding import ProjectEnvironmentBinding
from app.models.server import Environment
from app.models.user import User, UserRole

# Module 9 — centralized so every medallion route and sub-resource (datasets, preview,
# build, run, pause/unpause, lineage, runs, quality, versions) enforces the same rule
# instead of each route reimplementing its own check. 404 (never 403) on denial: a
# non-owner must not learn that a project even exists.


def get_owned_project(pid: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> MedallionProject:
    """Write access. Ownership governs writes, not role — even an admin only ever
    mutates their own projects (the module's golden rule)."""
    project = db.get(MedallionProject, pid)
    if project is None or project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")
    return project


def get_readable_project(pid: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> MedallionProject:
    """Read access. Owner, or any admin (supervision — read-only, never a write path)."""
    project = db.get(MedallionProject, pid)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")
    if project.owner_id != current_user.id and current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")
    return project


def _parse_environment(environment: str) -> Environment:
    try:
        return Environment(environment)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Environnement invalide : « {environment} ».")


def get_project_binding(pid: int, environment: str, db: Session, project: MedallionProject) -> ProjectEnvironmentBinding:
    """Module 17 §3.5 — resolves the binding for one (project, environment). Callers pass in
    an already-checked `project` (get_owned_project for a write, get_readable_project for a
    read) so ownership is enforced exactly once per request, at the project level, same as
    every other medallion sub-resource. 404 (never a separate "binding not found" shape) if
    this project simply has no binding for that environment yet — e.g. no prod binding
    before the first promotion — indistinguishable from "doesn't exist" to the caller."""
    env = _parse_environment(environment)
    binding = (
        db.query(ProjectEnvironmentBinding)
        .filter(ProjectEnvironmentBinding.project_id == project.id, ProjectEnvironmentBinding.environment == env)
        .first()
    )
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Aucun binding « {environment} » pour ce projet.")
    return binding


def get_owned_folder(fid: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> MedallionFolder:
    """Module 15 §3.1 — folders are strictly personal: unlike get_readable_project, there is
    no admin-supervision read path here at all (role is irrelevant, even an admin only ever
    sees/manages their own folders). 404 (never 403) on denial, same reasoning as
    get_owned_project — a non-owner must not learn a folder even exists."""
    folder = db.get(MedallionFolder, fid)
    if folder is None or folder.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier introuvable.")
    return folder
