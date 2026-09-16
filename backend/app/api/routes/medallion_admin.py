from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.routes.medallion import build_lineage_graph
from app.db.session import get_db
from app.models.medallion import MedallionDataset, MedallionProject, MedallionRun
from app.models.user import User
from app.schemas.medallion import DatasetOut, LineageGraph, ProjectOut, RunOut
from app.schemas.user import UserOut

# Module 9 — read-only supervision "by user". Every route here is admin-only and never
# mutates a project: writing to a project always goes through /api/medallion/projects/*,
# gated by ownership (get_owned_project), even for an admin.
router = APIRouter(prefix="/api/medallion/admin", tags=["medallion-admin"], dependencies=[Depends(require_admin)])


class AdminProjectSummaryOut(BaseModel):
    id: int
    name: str
    status: str
    schedule: str | None = None
    has_pending_changes: bool
    last_run: RunOut | None = None


class AdminOwnerGroupOut(BaseModel):
    owner: UserOut
    projects: list[AdminProjectSummaryOut]


class AdminProjectDetailOut(BaseModel):
    project: ProjectOut
    owner: UserOut
    datasets: list[DatasetOut]
    lineage: LineageGraph


@router.get("/overview", response_model=list[AdminOwnerGroupOut])
def get_admin_overview(db: Session = Depends(get_db)):
    projects = db.query(MedallionProject).order_by(MedallionProject.owner_id, MedallionProject.created_at.asc()).all()
    if not projects:
        return []

    owner_ids = {p.owner_id for p in projects}
    owners = {u.id: u for u in db.query(User).filter(User.id.in_(owner_ids)).all()}

    # One query for the latest run per project instead of N+1 — first row per project_id
    # wins since the query is already ordered newest-first.
    project_ids = [p.id for p in projects]
    last_runs: dict[int, MedallionRun] = {}
    all_runs = (
        db.query(MedallionRun)
        .filter(MedallionRun.project_id.in_(project_ids))
        .order_by(MedallionRun.project_id, MedallionRun.created_at.desc())
        .all()
    )
    for run in all_runs:
        last_runs.setdefault(run.project_id, run)

    grouped: dict[int, list[MedallionProject]] = {}
    for p in projects:
        grouped.setdefault(p.owner_id, []).append(p)

    result = []
    for owner_id, owner_projects in grouped.items():
        owner = owners.get(owner_id)
        if owner is None:
            continue  # orphaned owner_id (deleted account) — never expected, skip defensively
        result.append(AdminOwnerGroupOut(
            owner=UserOut.model_validate(owner),
            projects=[
                AdminProjectSummaryOut(
                    id=p.id, name=p.name, status=p.status.value, schedule=p.schedule,
                    has_pending_changes=p.has_pending_changes,
                    last_run=RunOut.model_validate(last_runs[p.id]) if p.id in last_runs else None,
                )
                for p in owner_projects
            ],
        ))
    result.sort(key=lambda g: g.owner.name.lower())
    return result


@router.get("/projects/{pid}", response_model=AdminProjectDetailOut)
def get_admin_project_detail(pid: int, db: Session = Depends(get_db)):
    project = db.get(MedallionProject, pid)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")
    owner = db.get(User, project.owner_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Propriétaire introuvable.")
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == pid).order_by(MedallionDataset.created_at.asc()).all()
    lineage = build_lineage_graph(db, datasets)
    return AdminProjectDetailOut(
        project=ProjectOut.model_validate(project),
        owner=UserOut.model_validate(owner),
        datasets=[DatasetOut.model_validate(d) for d in datasets],
        lineage=lineage,
    )
