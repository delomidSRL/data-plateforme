"""Module 13 §6 — sequential, gated execution of a validated plan through the existing
deterministic chain (Module 3 build/run, Module 11 publish, Module 12 dashboard hand-off).
Never calls Mistral — the persisted plan is the only input, so replaying/resuming is always
deterministic. A step is only marked done once its result is durably recorded in
execution_state, so re-entering (POST /execute called again — after a silver approval, after
a run finishes, or just to poll) always resumes exactly where it left off rather than
re-running finished work. The resulting datasets are created via the same construction the
manual "Ajouter un dataset" flow uses (see medallion_crud.upsert_dataset_internal), so a
plan-built project is indistinguishable from a hand-built one (§6.5.4) — upserting by
(project, layer, name) rather than always inserting, since execution_state.dataset_ids gets
wiped every time the engineer re-runs map-intent/plan on the same project, and blindly
re-inserting would otherwise leave duplicate, same-named datasets behind."""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.models.medallion import LoadMode, MedallionDataset, MedallionLayer, MedallionProject, MedallionRun, Materialization, ProjectStatus, RunState
from app.models.pipeline_plan import PipelinePlan, PipelinePlanStatus
from app.models.user import User
from app.schemas.medallion import DatasetCreate
from app.services import airflow_api, airflow_instances, gold_profile, indicator_suggest, superset_publish, version_snapshot, run_status
from app.services.ai_config import get_ai_config
from app.services.medallion_crud import upsert_dataset_internal
from app.services.medallion_deploy import build_project

logger = logging.getLogger("app.pipeline_execute")


class ExecutionError(Exception):
    def __init__(self, step: str, message: str):
        self.step = step
        self.message = message
        super().__init__(message)


def _resolve_target(db: Session, project: MedallionProject) -> airflow_instances.DeployTarget:
    try:
        instance = airflow_instances.resolve_project_instance(db, project)
        return airflow_instances.resolve_deploy_target(db, instance, project.dbt_project_name)
    except airflow_instances.AirflowInstanceError as exc:
        raise ExecutionError("build", str(exc)) from exc


def _create_datasets(db: Session, project: MedallionProject, plan: dict) -> dict[str, int]:
    """Bronze, then silver, then gold, in that order — each layer's upstream_dataset_ids
    resolve against real ids already assigned to the previous layer. relationships tests
    translate their plan-local `to` into the real ref()/source() dbt expects; a target not
    yet created when its referencing dataset is built (shouldn't happen given resolve_plan's
    own validation, but layer ordering makes it theoretically possible) is silently dropped —
    same "invalid entries never reach the build" philosophy as the plan's own validation."""
    ids: dict[str, int] = {}
    layer_of: dict[str, MedallionLayer] = {}

    def tests_for(name: str) -> list[dict]:
        out = []
        for t in plan["tests"]:
            if t["dataset"] != name or not t.get("include", True):
                continue
            entry: dict = {"column": t["column"], "test": t["test"]}
            if t["test"] == "accepted_values":
                entry["values"] = t.get("values", [])
            elif t["test"] == "relationships":
                target_name = t.get("to")
                target_layer = layer_of.get(target_name)
                if target_layer is None:
                    continue
                entry["to"] = f"source('bronze', '{target_name}')" if target_layer == MedallionLayer.bronze else f"ref('{target_name}')"
                entry["field"] = t.get("field")
            out.append(entry)
        return out

    for b in plan["bronze"]:
        payload = DatasetCreate(
            layer=MedallionLayer.bronze, name=b["name"], source_id=b["source_id"], source_object=b["table"],
            load_mode=LoadMode(b["mode"]), incremental_key=b.get("incremental_key"), tests=tests_for(b["name"]),
        )
        dataset = upsert_dataset_internal(db, project, payload)
        ids[b["name"]] = dataset.id
        layer_of[b["name"]] = MedallionLayer.bronze

    for s in plan["silver"]:
        payload = DatasetCreate(
            layer=MedallionLayer.silver, name=s["name"], dbt_model_name=s["name"], materialization=Materialization.view,
            sql=s["sql"], upstream_dataset_ids=[ids[u] for u in s["upstreams"] if u in ids],
            tests=tests_for(s["name"]), description=s.get("rationale") or None,
        )
        dataset = upsert_dataset_internal(db, project, payload)
        ids[s["name"]] = dataset.id
        layer_of[s["name"]] = MedallionLayer.silver

    for g in plan["gold"]:
        payload = DatasetCreate(
            layer=MedallionLayer.gold, name=g["name"], dbt_model_name=g["name"], materialization=Materialization.table,
            sql=g["sql"], upstream_dataset_ids=[ids[u] for u in g["upstreams"] if u in ids],
            tests=tests_for(g["name"]),
        )
        dataset = upsert_dataset_internal(db, project, payload)
        ids[g["name"]] = dataset.id
        layer_of[g["name"]] = MedallionLayer.gold

    return ids


def sync_dataset_sql(db: Session, plan_row: PipelinePlan, silver_name: str) -> None:
    """Called when a silver is approved mid-execution (POST /silver/{did}/approve) — if the
    engineer edited its SQL via PATCH /plan after create_datasets() already ran, the real
    MedallionDataset row still has the SQL as it was at creation time. Without this, the
    build would compile the stale, pre-edit version."""
    dataset_ids = (plan_row.execution_state or {}).get("dataset_ids") or {}
    did = dataset_ids.get(silver_name)
    if did is None:
        return
    entry = next((s for s in (plan_row.plan or {}).get("silver", []) if s["name"] == silver_name), None)
    if entry is None:
        return
    dataset = db.get(MedallionDataset, did)
    if dataset is not None and dataset.sql != entry["sql"]:
        dataset.sql = entry["sql"]


async def execute_plan(db: Session, project: MedallionProject, plan_row: PipelinePlan, current_user: User) -> PipelinePlan:
    if plan_row.plan is None:
        raise ExecutionError("create_datasets", "Aucun plan à exécuter.")

    state = dict(plan_row.execution_state or {})
    plan = plan_row.plan

    # "Reprendre" after a failure always retries from the build (§6.5.3) — never from
    # scratch (dataset_ids stays untouched below) but never by just re-triggering the same
    # stale run either, since the whole point of a retry is that something (usually a silver
    # SQL) changed since the failing build/run and needs to be picked up.
    if plan_row.status == PipelinePlanStatus.run_failed:
        state.pop("built_at", None)
        state.pop("run_id", None)
        state.pop("failed_step", None)
        state.pop("error", None)

    try:
        # 1. create_datasets — ordinary MedallionDataset rows. Re-synced from the current plan
        # every time we're about to (re)build, not just on first creation: a plan edited after
        # dataset_ids was first populated (e.g. a corrected test column, a tweaked silver SQL
        # via PATCH /plan) would otherwise never reach the real MedallionDataset rows that
        # build_project() actually reads from, since upsert_dataset_internal only runs here.
        # Skipped once built_at is set (mid-run polling) so this doesn't re-upsert on every
        # POST /execute the frontend fires while waiting for a run to finish.
        if "dataset_ids" not in state or not state.get("built_at"):
            state["step"] = "create_datasets"
            state["dataset_ids"] = _create_datasets(db, project, plan)
            db.commit()

        # 2. gate_silver — stop on the first pass where any silver isn't yet approved. An
        # already-approved plan (approved from the plan screen, before execution started)
        # sails through this with zero stops, per §6.5.2.
        pending = [s["name"] for s in plan["silver"] if s.get("status") != "approved"]
        if pending:
            plan_row.status = PipelinePlanStatus.waiting_approval
            state["step"] = "gate_silver"
            state["pending_silver"] = pending
            plan_row.execution_state = state
            db.commit()
            db.refresh(plan_row)
            return plan_row

        plan_row.status = PipelinePlanStatus.executing
        state["pending_silver"] = []

        # 3. build — Module 3's own build_project(), synchronous (SFTP + Airflow API calls,
        # not a long-running job — same as the existing POST /{pid}/build route).
        if not state.get("built_at"):
            state["step"] = "build"
            plan_row.execution_state = state
            db.commit()

            target = _resolve_target(db, project)
            datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
            # Module 17 — the agent always builds the project's home/dev binding (promotion
            # to prod is a separate, explicit gesture, never something an execution plan does).
            binding = project.home_binding
            warehouse = db.get(DataSource, binding.warehouse_source_id)
            object_store = db.get(DataSource, binding.object_store_source_id)
            try:
                report = await build_project(db, project, binding, datasets, warehouse, object_store, target)
            except Exception as exc:
                raise ExecutionError("build", str(exc)) from exc

            binding.dag_id = report["dag_id"]
            binding.dag_file_path = report["dag_path"]
            binding.status = ProjectStatus.deployed
            project.has_pending_changes = False
            db.commit()
            try:
                version_snapshot.capture(
                    db, project, binding, datasets, report["dbt_files_content"], report["dag_content"],
                    report["dag_id_base"], report["dag_path"], current_user,
                )
            except Exception:
                logger.exception("version snapshot capture failed for project %s (Module 13 execution)", project.id)

            state["built_at"] = datetime.now(timezone.utc).isoformat()
            plan_row.execution_state = state
            db.commit()

        # 4. run — trigger once; on every re-entry (including a fresh POST /execute the
        # frontend fires while polling), refresh the run's real state from Airflow before
        # deciding whether to advance.
        state["step"] = "run"
        if not state.get("run_id"):
            target = _resolve_target(db, project)
            try:
                # A DAG with no schedule stays paused after deploy by design (Module 3
                # correctif — a manual project must rest paused between runs). But Airflow's
                # scheduler never executes ANY run, including a manually-triggered one, while
                # its DAG is paused — it just sits in "queued" forever with no error. "Run
                # now" is exactly the case where the pause must be lifted for the trigger to
                # actually do anything.
                dag = await airflow_api.get_dag(target.airflow.base_url, target.airflow.username, target.airflow.password, project.dag_id)
                if dag is not None and dag.get("is_paused"):
                    await airflow_api.set_dag_paused(target.airflow.base_url, target.airflow.username, target.airflow.password, project.dag_id, False)
                result = await airflow_api.trigger_dag_run(target.airflow.base_url, target.airflow.username, target.airflow.password, project.dag_id, None)
            except airflow_api.AirflowAPIError as exc:
                if exc.status_code == 404:
                    # The DAG file was just deposited by build() — Airflow's dag-processor
                    # hasn't scanned it yet (same reparse-latency reality as the Module 3
                    # correctif's deploy-status polling). Not a failure: stay "executing" and
                    # let the caller retry — same non-blocking-retry shape as the run-still-
                    # in-progress branch just below.
                    plan_row.status = PipelinePlanStatus.executing
                    plan_row.execution_state = state
                    db.commit()
                    db.refresh(plan_row)
                    return plan_row
                raise ExecutionError("run", str(exc)) from exc
            run = MedallionRun(project_id=project.id, dag_run_id=result.get("dag_run_id"), state=RunState.queued, started_at=datetime.now(timezone.utc))
            db.add(run)
            db.commit()
            db.refresh(run)
            state["run_id"] = run.id
            plan_row.execution_state = state
            db.commit()

        run = db.get(MedallionRun, state["run_id"])
        target = _resolve_target(db, project)
        run = await run_status.refresh_run_state(db, project, run, target)

        if run.state in (RunState.queued, RunState.running):
            plan_row.status = PipelinePlanStatus.executing
            plan_row.execution_state = state
            db.commit()
            db.refresh(plan_row)
            return plan_row  # not finished yet — the caller polls and calls /execute again

        if run.state == RunState.failed:
            raise ExecutionError("run", "Le run dbt a échoué — voir les logs de ce run dans l'onglet Runs.")

        # 5. publish — every gold dataset, to the project's own Superset instance.
        if not state.get("published_at"):
            state["step"] = "publish"
            plan_row.execution_state = state
            db.commit()

            dataset_ids = state["dataset_ids"]
            published = []
            for g in plan["gold"]:
                did = dataset_ids.get(g["name"])
                if did is None:
                    continue
                dataset = db.get(MedallionDataset, did)
                try:
                    outcome = await superset_publish.publish_dataset(db, project, dataset, published_by_id=project.owner_id, instance=None)
                except Exception as exc:
                    raise ExecutionError("publish", str(exc)) from exc
                if outcome.status != "ok":
                    raise ExecutionError("publish", outcome.message or f"Publication Superset en échec ({outcome.status}).")
                published.append({"dataset_id": did, "name": g["name"], "url": outcome.url})

            state["published_at"] = datetime.now(timezone.utc).isoformat()
            state["published"] = published
            plan_row.execution_state = state
            db.commit()

        # 6. dashboard — proposes indicators for every published gold right here in the
        # agent's own chain (no more manual "go to Pipeline tab, open the gold dataset,
        # click Indicateurs" detour), then pauses for a real human validation step — same
        # gate shape as step 2's gate_silver, just reusing Module 12's own suggestion/
        # generation services (indicator_suggest.py, superset_publish.py) instead of
        # duplicating them. POST /agent/dashboard/generate (medallion_agent.py) submits the
        # validated/edited list and actually builds the charts, advancing to "done".
        state["step"] = "dashboard"
        if "dashboard_review" not in state:
            config = get_ai_config()
            dataset_ids = state["dataset_ids"]
            review: dict[str, dict] = {}
            for g in plan["gold"]:
                did = dataset_ids.get(g["name"])
                if did is None:
                    continue
                dataset = db.get(MedallionDataset, did)
                profile_result = gold_profile.profile_gold_dataset(db, project, dataset)
                if profile_result.status != "ok":
                    continue
                result = await indicator_suggest.suggest_indicators(config, profile_result.columns, plan_row.instruction)
                review[str(did)] = {
                    "dataset_name": g["name"],
                    "source": result.source,
                    "warnings": result.warnings,
                    "columns": [{"name": c.name, "role": c.role} for c in profile_result.columns],
                    "indicators": [
                        {
                            "title": i.title, "viz_type": i.viz_type, "metric_column": i.metric_column,
                            "aggregation": i.aggregation, "dimension_columns": i.dimension_columns,
                            "time_column": i.time_column, "slots": i.slots, "ranking": i.ranking, "included": True,
                        }
                        for i in result.indicators
                    ],
                }
            state["dashboard_review"] = review
            plan_row.status = PipelinePlanStatus.waiting_approval
            plan_row.execution_state = state
            db.commit()
            db.refresh(plan_row)
            return plan_row

        if not state.get("dashboard_generated_at"):
            # Still waiting on POST /agent/dashboard/generate to submit the validated list.
            plan_row.status = PipelinePlanStatus.waiting_approval
            plan_row.execution_state = state
            db.commit()
            db.refresh(plan_row)
            return plan_row

        plan_row.status = PipelinePlanStatus.done
        plan_row.execution_state = state
        db.commit()

    except ExecutionError as exc:
        plan_row.status = PipelinePlanStatus.run_failed
        state["failed_step"] = exc.step
        state["error"] = exc.message
        plan_row.execution_state = state
        db.commit()

    db.refresh(plan_row)
    return plan_row
