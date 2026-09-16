from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.data_source import DataSource, DataSourceStatus
from app.models.infra_stack import InfraStack, StackStatus
from app.models.medallion import MedallionProject, MedallionRun, RunState
from app.models.server import Server, ServerStatus
from app.models.user import User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

RECENT_RUNS_LIMIT = 8
ALERTS_LIMIT = 20


@router.get("/overview")
def get_overview(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    servers = db.query(Server).order_by(Server.name.asc()).all()
    stacks = db.query(InfraStack).order_by(InfraStack.name.asc()).all()
    sources = db.query(DataSource).all()
    projects = {p.id: p for p in db.query(MedallionProject).all()}

    active_runs = (
        db.query(MedallionRun)
        .filter(MedallionRun.state.in_([RunState.queued, RunState.running]))
        .order_by(MedallionRun.started_at.desc())
        .all()
    )
    recent_runs = (
        db.query(MedallionRun)
        .filter(MedallionRun.state.in_([RunState.success, RunState.failed]))
        .order_by(MedallionRun.finished_at.desc().nullslast(), MedallionRun.created_at.desc())
        .limit(RECENT_RUNS_LIMIT)
        .all()
    )

    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    runs_24h = db.query(MedallionRun).filter(MedallionRun.created_at >= since_24h).all()
    success_24h = sum(1 for r in runs_24h if r.state == RunState.success)
    failed_24h = sum(1 for r in runs_24h if r.state == RunState.failed)

    def run_out(r: MedallionRun) -> dict:
        project = projects.get(r.project_id)
        return {
            "id": r.id,
            "project_id": r.project_id,
            "project_name": project.name if project else "—",
            "state": r.state.value,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }

    alerts = []
    for s in servers:
        if s.status == ServerStatus.unreachable:
            alerts.append({"severity": "error", "message": f"Serveur « {s.name} » injoignable.", "path": f"/servers/{s.id}"})
    for st in stacks:
        if st.status == StackStatus.error:
            alerts.append({"severity": "error", "message": f"Stack « {st.name} » en erreur : {st.last_error or 'voir détails'}", "path": f"/servers/{st.server_id}"})
    for src in sources:
        if src.status == DataSourceStatus.unreachable:
            alerts.append({"severity": "warn", "message": f"Source « {src.name} » injoignable.", "path": "/sources"})
    for p in projects.values():
        if p.has_pending_changes:
            alerts.append({"severity": "info", "message": f"Projet « {p.name} » a des changements non déployés.", "path": f"/medallion/{p.id}"})

    latest_run_by_project: dict[int, MedallionRun] = {}
    all_runs_desc = db.query(MedallionRun).order_by(MedallionRun.created_at.desc()).all()
    for r in all_runs_desc:
        latest_run_by_project.setdefault(r.project_id, r)
    for pid, r in latest_run_by_project.items():
        if r.state == RunState.failed:
            project = projects.get(pid)
            alerts.append({"severity": "error", "message": f"Dernier run de « {project.name if project else pid} » en échec.", "path": f"/medallion/{pid}"})

    return {
        "servers": {
            "total": len(servers),
            "reachable": sum(1 for s in servers if s.status == ServerStatus.reachable),
            "unreachable": sum(1 for s in servers if s.status == ServerStatus.unreachable),
            "items": [{"id": s.id, "name": s.name, "hostname": s.hostname, "status": s.status.value} for s in servers],
        },
        "stacks": {
            "total": len(stacks),
            "running": sum(1 for s in stacks if s.status == StackStatus.running),
            "error": sum(1 for s in stacks if s.status == StackStatus.error),
            "items": [{"id": s.id, "server_id": s.server_id, "name": s.name, "status": s.status.value} for s in stacks],
        },
        "sources": {
            "total": len(sources),
            "reachable": sum(1 for s in sources if s.status == DataSourceStatus.reachable),
            "unreachable": sum(1 for s in sources if s.status == DataSourceStatus.unreachable),
        },
        "medallion": {
            "projects_total": len(projects),
            "active_runs": [run_out(r) for r in active_runs],
            "recent_runs": [run_out(r) for r in recent_runs],
            "last_24h": {"success": success_24h, "failed": failed_24h},
        },
        "alerts": alerts[:ALERTS_LIMIT],
        "generated_at": datetime.now(timezone.utc),
    }
