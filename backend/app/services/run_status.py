"""Live Airflow run-status refresh — shared by the HTTP route (routes/medallion.py's
GET /{pid}/runs/{run_id}) and the Module 13 execution machine (services/pipeline_execute.py),
which needs to know when a triggered run has actually finished before moving on to publish."""
import logging
from datetime import datetime

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionProject, MedallionRun, RunState
from app.services import airflow_api
from app.services.airflow_instances import DeployTarget
from app.services.medallion_stats import refresh_dataset_stats
from app.services.quality_collector import collect_quality
from app.services.quality_notify import notify_alerts
from app.services.quality_rules import evaluate_coherence
from app.services import quality_intrinsic

logger = logging.getLogger("app.medallion")


async def refresh_run_state(db: Session, project: MedallionProject, run: MedallionRun, target: DeployTarget) -> MedallionRun:
    if not (run.dag_run_id and run.state in (RunState.queued, RunState.running)):
        return run
    try:
        remote = await airflow_api.get_dag_run(target.airflow.base_url, target.airflow.username, target.airflow.password, project.dag_id, run.dag_run_id)
    except airflow_api.AirflowAPIError:
        return run

    state_map = {"success": RunState.success, "failed": RunState.failed, "running": RunState.running, "queued": RunState.queued}
    new_state = state_map.get(remote.get("state"), run.state)
    # Only success runs get a stats refresh: bronze/silver/gold tables and run_results.json
    # reflect whatever the *latest* run did, not this specific one, so attaching them to a
    # failed (or stale historical) run would be misleading.
    just_finished = new_state == RunState.success and run.state != new_state
    # Quality collection (Module 5) is separate and broader: it fires on success OR failure —
    # a failing run still tells the truth about the *current* state of the gold table.
    just_reached_terminal_state = new_state in (RunState.success, RunState.failed) and run.state != new_state
    run.state = new_state
    if remote.get("end_date"):
        run.finished_at = datetime.fromisoformat(remote["end_date"].replace("Z", "+00:00"))
    db.commit()

    pid = project.id
    if just_finished:
        datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == pid).all()
        warehouse = db.get(DataSource, project.warehouse_source_id)
        try:
            stats = await run_in_threadpool(refresh_dataset_stats, db, project, datasets, warehouse, target)
            run.layer_stats = stats["layer_stats"]
            run.tests_summary = stats["tests_summary"]
            db.commit()
        except Exception:
            pass

    if just_reached_terminal_state:
        try:
            datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == pid).all()
            warehouse = db.get(DataSource, project.warehouse_source_id)
            await run_in_threadpool(collect_quality, db, project, datasets, warehouse, target, run.id, run.finished_at)
        except Exception:
            logger.warning("quality collection failed for run %s (project %s)", run.id, pid, exc_info=True)
        # Module 16 §4.3 — same post-run passage, right after M5's 4 signals. Independent
        # try/except: an intrinsic-quality failure never rolls back or hides the M5 collection
        # that just succeeded above.
        try:
            datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == pid).all()
            warehouse = db.get(DataSource, project.warehouse_source_id)
            metrics = await run_in_threadpool(quality_intrinsic.collect, db, project, datasets, warehouse, run)
            # §8.3 — same run, right after the metrics that feed it: a coherence alert always
            # reflects THIS run's own freshly-computed defect rates, never a stale prior one.
            coherence_alerts = evaluate_coherence(db, pid, metrics, {d.id: d.name for d in datasets})
            notify_alerts(db, project, coherence_alerts)
        except Exception:
            logger.warning("intrinsic quality collection failed for run %s (project %s)", run.id, pid, exc_info=True)

    db.refresh(run)
    return run
