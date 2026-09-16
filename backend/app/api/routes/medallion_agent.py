import copy
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.deps_medallion import get_owned_project, get_readable_project
from app.core.config import get_settings
from app.db.session import get_db
from app.models.medallion import MedallionDataset, MedallionProject
from app.models.pipeline_plan import PipelinePlan, PipelinePlanStatus
from app.models.user import User
from app.schemas.pipeline_agent import GenerateAgentDashboardsRequest, MapIntentRequest, PipelinePlanOut, PlanPatch, RelationshipOut, ReplanRequest
from app.services import ai_client, intent_mapping, pipeline_execute, pipeline_plan, relationship_detect, source_profile, superset_publish
from app.services.ai_config import get_ai_config

router = APIRouter(prefix="/api/medallion/projects/{pid}/agent", tags=["medallion-agent"])


def _with_ai_debug(plan: PipelinePlan) -> PipelinePlanOut:
    """Annexe "élargir le scope de l'assistant IA" §debug — attaches this request's captured
    AI call(s) (see ai_client.reset_debug_log/get_debug_log) to the plan response, for the
    browser DevTools. Every route that can trigger an AI call resets the log near its own
    start and returns through this instead of the bare ORM object."""
    out = PipelinePlanOut.model_validate(plan)
    out.ai_debug = ai_client.get_debug_log()
    return out

# A plan still in the mapping phase can be freely restarted from a new instruction. Once it
# has moved past mapping (a plan was generated, or execution started), /map-intent must not
# silently blow it away — /replan (Étape 3) is the explicit, confirmed way to do that.
_RESTARTABLE_STATUSES = (PipelinePlanStatus.mapping, PipelinePlanStatus.mapping_failed)

# A plan can be (re)generated as long as execution hasn't started yet — once it has
# (executing/waiting_approval/done/run_failed), generating a fresh plan out from under it
# would silently invalidate datasets that may already exist. plan_failed stays generatable
# so a failed attempt can simply be retried.
_PLAN_GENERATABLE_STATUSES = (PipelinePlanStatus.mapping, PipelinePlanStatus.planned, PipelinePlanStatus.plan_failed)


def _get_plan(db: Session, project_id: int) -> PipelinePlan | None:
    return db.query(PipelinePlan).filter(PipelinePlan.project_id == project_id).first()


async def _run_mapping(db: Session, project_id: int, instruction: str) -> dict:
    ai_client.reset_debug_log()
    settings = get_settings()
    profiles = source_profile.build_profiles(db, project_id=project_id, instruction=instruction, max_tables=settings.pipeline_agent_max_tables)
    if not profiles:
        return {"status": "unresolved", "tables": [], "unresolved": [{"need": instruction, "candidates": []}]}

    # Module 14 §4 — computed on the SAME bounded candidate set as the mapping itself (never
    # the whole schema), before the AI runs, so the AI reasons from verified relationships
    # instead of guessing joins from column-name resemblance alone.
    relationships = relationship_detect.compute_relationships(db, profiles)

    config = get_ai_config()
    try:
        raw = await intent_mapping.map_intent_ai(config, instruction, profiles, relationships)
    except intent_mapping.AgentAIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"IA indisponible pour le mapping : {exc}")
    resolved = intent_mapping.resolve_mapping(raw, profiles, settings.pipeline_agent_min_confidence)
    resolved["relationships"] = relationships
    return resolved


@router.post("/map-intent", response_model=PipelinePlanOut)
async def map_intent(pid: int, payload: MapIntentRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user)):
    plan = _get_plan(db, project.id)
    if plan is not None and plan.status not in _RESTARTABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Un plan existe déjà pour ce projet à un stade avancé — utilisez le re-planning depuis l'écran de plan.")

    mapping = await _run_mapping(db, project.id, payload.instruction)

    if plan is None:
        plan = PipelinePlan(project_id=project.id, instruction=payload.instruction, status=PipelinePlanStatus.mapping, execution_state={}, created_by=current_user.id, mapping=mapping)
        db.add(plan)
    else:
        plan.instruction = payload.instruction
        plan.status = PipelinePlanStatus.mapping
        plan.execution_state = {}
        plan.mapping = mapping

    db.commit()
    db.refresh(plan)
    return _with_ai_debug(plan)


@router.post("/remap", response_model=PipelinePlanOut)
async def remap(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), _: User = Depends(get_current_user)):
    plan = _get_plan(db, project.id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun mapping en cours pour ce projet — lancez d'abord l'analyse.")

    settings = get_settings()
    remap_count = (plan.execution_state or {}).get("remap_count", 0)
    if remap_count >= settings.pipeline_agent_max_remap_iterations:
        plan.status = PipelinePlanStatus.mapping_failed
        db.commit()
        db.refresh(plan)
        return _with_ai_debug(plan)

    mapping = await _run_mapping(db, project.id, plan.instruction)
    plan.mapping = mapping
    plan.status = PipelinePlanStatus.mapping
    plan.execution_state = {**(plan.execution_state or {}), "remap_count": remap_count + 1}
    db.commit()
    db.refresh(plan)
    return _with_ai_debug(plan)


@router.get("/plan", response_model=PipelinePlanOut)
def get_plan(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    plan = _get_plan(db, project.id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun plan pour ce projet.")
    return plan


def _mapping_source_ids(mapping: dict) -> list[int]:
    ids = {t["source_id"] for t in mapping.get("tables", [])}
    ids |= {c["source_id"] for u in mapping.get("unresolved", []) for c in u.get("candidates", [])}
    return list(ids)


@router.post("/relationships/recompute", response_model=list[RelationshipOut])
def recompute_relationships(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), _: User = Depends(get_current_user)):
    """Module 14 §4.6 — the explicit "Recalculer les relations" button: ignores the
    schema-fingerprint cache and re-queries every candidate pair for this project's sources."""
    plan = _get_plan(db, project.id)
    if plan is None or not plan.mapping:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun mapping pour ce projet — lancez d'abord l'analyse.")

    settings = get_settings()
    profiles = source_profile.build_profiles(db, project_id=project.id, instruction=plan.instruction, max_tables=settings.pipeline_agent_max_tables)
    relationships = relationship_detect.compute_relationships(db, profiles, force=True)

    mapping = copy.deepcopy(plan.mapping)
    mapping["relationships"] = relationships
    plan.mapping = mapping
    db.commit()
    return relationships


@router.get("/relationships", response_model=list[RelationshipOut])
def get_relationships(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 14 §4.6 — cached relations only (read, project visibility), no recompute."""
    plan = _get_plan(db, project.id)
    if plan is None or not plan.mapping:
        return []
    return relationship_detect.get_cached_relationships(db, _mapping_source_ids(plan.mapping))


async def _generate_plan(db: Session, plan: PipelinePlan) -> PipelinePlanOut:
    if not plan.mapping or not plan.mapping.get("tables"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le mapping ne contient aucune table résolue — impossible de générer un plan.")

    ai_client.reset_debug_log()
    config = get_ai_config()
    try:
        raw, bronze_names, prebuilt_silver = await pipeline_plan.generate_plan_ai(db, config, plan.instruction, plan.mapping)
    except pipeline_plan.PlanAIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"IA indisponible pour la génération du plan : {exc}")

    # Module 14 §7 — bounded repair loop: fixes AI-authored silver sql in place (raw.silver)
    # before resolve_plan() runs its own (unchanged) validation, so more of them survive.
    settings = get_settings()
    repair_log = await pipeline_plan.repair_plan_silvers(db, config, raw, plan.mapping, bronze_names, prebuilt_silver, settings.repair_max_attempts)

    resolved = pipeline_plan.resolve_plan(raw, plan.mapping, bronze_names, prebuilt_silver, plan.instruction)
    resolved["repair_log"] = repair_log
    plan.plan = resolved
    plan.status = PipelinePlanStatus.planned if pipeline_plan.is_plan_valid(resolved) else PipelinePlanStatus.plan_failed
    db.commit()
    db.refresh(plan)
    return _with_ai_debug(plan)


@router.post("/plan", response_model=PipelinePlanOut)
async def generate_plan_endpoint(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), _: User = Depends(get_current_user)):
    plan = _get_plan(db, project.id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun mapping pour ce projet — lancez d'abord l'analyse.")
    if plan.status not in _PLAN_GENERATABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le plan est à un stade avancé — utilisez le replan pour le régénérer.")
    return await _generate_plan(db, plan)


@router.post("/replan", response_model=PipelinePlanOut)
async def replan(pid: int, payload: ReplanRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), _: User = Depends(get_current_user)):
    if not payload.confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Confirmation requise pour remplacer le plan actuel.")
    plan = _get_plan(db, project.id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun mapping pour ce projet — lancez d'abord l'analyse.")
    if plan.status not in _PLAN_GENERATABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Impossible de re-planifier un plan déjà en cours d'exécution.")
    return await _generate_plan(db, plan)


@router.post("/silver/{silver_name}/repair", response_model=PipelinePlanOut)
async def repair_silver_endpoint(pid: int, silver_name: str, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), _: User = Depends(get_current_user)):
    """Module 14 §7.4 — optional, on-demand repair for one silver already in the plan (e.g.
    the engineer suspects a manual edit broke it), without regenerating the whole plan. Owner
    write; 404 if no plan or the name isn't in it."""
    plan_row = _get_plan(db, project.id)
    if plan_row is None or not plan_row.plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun plan à réparer — générez-en un d'abord.")

    plan = copy.deepcopy(plan_row.plan)
    ai_client.reset_debug_log()
    settings = get_settings()
    config = get_ai_config()
    bronze_names = {b["table"]: b["name"] for b in plan["bronze"]}
    try:
        result = await pipeline_plan.repair_one_silver(db, config, plan, plan_row.mapping, bronze_names, silver_name, settings.repair_max_attempts)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if result["log"] and result["log"]["status"] == "repaired":
        entry = result["entry"]
        entry["status"], entry["approved_by"], entry["approved_at"] = "draft", None, None

    plan_row.plan = plan
    db.commit()
    db.refresh(plan_row)
    return _with_ai_debug(plan_row)


@router.patch("/plan", response_model=PipelinePlanOut)
def patch_plan(pid: int, payload: PlanPatch, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user)):
    plan_row = _get_plan(db, project.id)
    if plan_row is None or not plan_row.plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun plan à modifier — générez-en un d'abord.")

    # Deep-copy before mutating: `plan_row.plan` is what SQLAlchemy's change tracking compares
    # against at flush time. Mutating that same object in place first (then reassigning it back
    # to itself) leaves the "before" and "after" values identical by content, so the JSONB
    # column's UPDATE gets silently skipped — commit() becomes a no-op despite no error.
    plan = copy.deepcopy(plan_row.plan)
    plan["bronze"] = [b for b in plan["bronze"] if b["name"] not in payload.remove_bronze]
    plan["silver"] = [s for s in plan["silver"] if s["name"] not in payload.remove_silver]
    plan["gold"] = [g for g in plan["gold"] if g["name"] not in payload.remove_gold]

    bronze_name_set = {b["name"] for b in plan["bronze"]}
    silver_by_name = {s["name"]: s for s in plan["silver"]}

    for edit in payload.silver_edits:
        entry = silver_by_name.get(edit.name)
        if entry is None:
            continue
        if edit.sql is not None and edit.sql != entry["sql"]:
            other_silver_names = set(silver_by_name.keys()) - {edit.name}
            validation = pipeline_plan.validate_silver_lineage(edit.sql, bronze_name_set, other_silver_names)
            if not validation.valid:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"SQL invalide pour « {edit.name} » : {'; '.join(validation.errors)}")
            entry["sql"] = edit.sql
            entry["status"], entry["approved_by"], entry["approved_at"] = "draft", None, None
        if edit.upstreams is not None:
            entry["upstreams"] = edit.upstreams
        if edit.approve is True:
            entry["status"], entry["approved_by"], entry["approved_at"] = "approved", current_user.id, datetime.now(timezone.utc).isoformat()
        elif edit.approve is False:
            entry["status"], entry["approved_by"], entry["approved_at"] = "draft", None, None

    for t in plan["tests"]:
        key = f"{t['dataset']}::{t['column']}::{t['test']}"
        if key in payload.test_toggles:
            t["include"] = payload.test_toggles[key]

    plan_row.plan = plan
    db.commit()
    db.refresh(plan_row)
    return plan_row


# ---------------- Étape 4 — execution ----------------

_EXECUTABLE_STATUSES = (PipelinePlanStatus.planned, PipelinePlanStatus.executing, PipelinePlanStatus.waiting_approval, PipelinePlanStatus.run_failed)


@router.post("/execute", response_model=PipelinePlanOut)
async def execute_endpoint(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user)):
    plan_row = _get_plan(db, project.id)
    if plan_row is None or not plan_row.plan or not pipeline_plan.is_plan_valid(plan_row.plan):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun plan valide à exécuter — générez d'abord un plan avec au moins un gold.")
    if plan_row.status not in _EXECUTABLE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce plan n'est pas dans un état exécutable.")
    ai_client.reset_debug_log()  # the "dashboard" step calls the AI (suggest_indicators) once
    result = await pipeline_execute.execute_plan(db, project, plan_row, current_user)
    return _with_ai_debug(result)


@router.get("/execution", response_model=PipelinePlanOut)
def get_execution(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    plan_row = _get_plan(db, project.id)
    if plan_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun plan pour ce projet.")
    return plan_row


@router.post("/silver/{dataset_id}/approve", response_model=PipelinePlanOut)
async def approve_silver_during_execution(pid: int, dataset_id: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user)):
    """Same effect as PATCH /plan's silver_edits approve, but for the specific case of
    approving a SQL the execution machine is currently stopped on (§6.3) — also syncs the
    real MedallionDataset's SQL (already created at this point) before resuming, in case the
    engineer edited it via PATCH /plan after create_datasets() already ran."""
    plan_row = _get_plan(db, project.id)
    if plan_row is None or not plan_row.plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun plan en cours d'exécution.")

    dataset_ids = (plan_row.execution_state or {}).get("dataset_ids") or {}
    name = next((n for n, did in dataset_ids.items() if did == dataset_id), None)
    if name is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset introuvable dans l'exécution en cours.")

    # Deep-copy before mutating — see the identical comment in patch_plan() above: mutating
    # plan_row.plan in place before reassigning it leaves SQLAlchemy's change tracking with
    # nothing to detect, so the commit silently persists nothing and the next attribute
    # access (expire_on_commit) reloads the stale, still-"draft" value from the database.
    plan = copy.deepcopy(plan_row.plan)
    entry = next((s for s in plan["silver"] if s["name"] == name), None)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Silver introuvable dans le plan.")

    entry["status"], entry["approved_by"], entry["approved_at"] = "approved", current_user.id, datetime.now(timezone.utc).isoformat()
    plan_row.plan = plan
    pipeline_execute.sync_dataset_sql(db, plan_row, name)

    # Approving here only ever happens after execution has already progressed past
    # create_datasets (this endpoint requires a real dataset_id from execution_state) — so
    # any previous build/run is now stale by definition and must be redone, regardless of
    # whether a prior call already recorded the failure as `run_failed` (execute_plan's own
    # status-based reset covers a plain retry via /execute; this covers the "fix it inline in
    # the stepper, without a separate failed /execute round-trip first" case).
    state = dict(plan_row.execution_state or {})
    state.pop("built_at", None)
    state.pop("run_id", None)
    state.pop("failed_step", None)
    state.pop("error", None)
    plan_row.execution_state = state
    db.commit()

    return await pipeline_execute.execute_plan(db, project, plan_row, current_user)


@router.post("/dashboard/generate", response_model=PipelinePlanOut)
async def generate_agent_dashboards(pid: int, payload: GenerateAgentDashboardsRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user)):
    """Submits the engineer's validated/edited version of execution_state.dashboard_review
    (proposed by execute_plan()'s "dashboard" step) and actually builds the Superset charts —
    the step that used to require leaving the Agent tab entirely for Module 12's own UI."""
    plan_row = _get_plan(db, project.id)
    if plan_row is None or "dashboard_review" not in (plan_row.execution_state or {}):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucune proposition de tableau de bord en attente — exécutez d'abord le plan jusqu'à l'étape « dashboard ».")

    state = copy.deepcopy(plan_row.execution_state)
    review = state["dashboard_review"]
    entries = []
    for ds in payload.datasets:
        dataset = db.get(MedallionDataset, ds.dataset_id)
        if dataset is None or dataset.project_id != project.id:
            continue
        indicators = [
            superset_publish.IndicatorSpec(
                title=i.title, viz_type=i.viz_type, metric_column=i.metric_column,
                aggregation=i.aggregation, dimension_columns=i.dimension_columns, time_column=i.time_column,
                slots=i.slots, ranking=i.ranking,
            )
            for i in ds.indicators
        ]
        if not indicators:
            continue
        source = review.get(str(ds.dataset_id), {}).get("source", "manual")
        entries.append((dataset, indicators, source))

    # Annexe "élargir le scope de l'assistant IA" — a multi-gold plan hands several datasets
    # to this endpoint at once; they're assembled into ONE shared project-level dashboard
    # (generate_combined_dashboard) instead of one per dataset, so the engineer gets a single
    # tableau de bord for the whole business objective rather than one fragment per gold mart.
    published = []
    if entries:
        try:
            outcome = await superset_publish.generate_combined_dashboard(db, project, entries, current_user.id)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Génération du tableau de bord impossible : {exc}")
        if outcome.status != "ok":
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=outcome.message or f"Génération du tableau de bord en échec ({outcome.status}).")
        published.append({"url": outcome.dashboard_url, "charts_count": outcome.charts_count})

    state["dashboard_generated_at"] = datetime.now(timezone.utc).isoformat()
    state["dashboards"] = published
    plan_row.execution_state = state
    plan_row.status = PipelinePlanStatus.done
    db.commit()
    db.refresh(plan_row)
    return plan_row
