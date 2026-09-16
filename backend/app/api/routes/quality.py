from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, require_admin
from app.api.deps_medallion import get_owned_project, get_readable_project
from app.db.session import get_db
from app.models.data_quality import AlertSeverity, AlertStatus, AlertType, CheckStatus, DataQualityAlert, DataQualityBaseline, DataQualityCheck, DataQualityMetric, DataQualitySnapshot, NotificationChannel, QualityIndicator, QualityLayer
from app.models.data_source import DataSource
from app.models.export_log import ExportKind
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, MedallionRun, RunState
from app.models.payload_structuration import PayloadStructuration
from app.models.user import User
from app.schemas.quality import (
    AlertOut,
    BaselineAssertions,
    BaselineOut,
    BaselineSuggestionOut,
    BaselineUpdate,
    ChannelCreate,
    ChannelOut,
    ChannelUpdate,
    CheckCreate,
    CheckOut,
    CheckSuggestOut,
    CheckUpdate,
    DatasetQualityOut,
    MetricOut,
    OverviewCellOut,
    OverviewIndicatorOut,
    OverviewLayerOut,
    OverviewTableOut,
    ProjectQualityOut,
    QualityOverviewOut,
    QualitySnapshotOut,
    RuleOut,
    RuleUpdate,
    SchemaDiffEntry,
)
from app.services import airflow_instances, dbt_export, expectation_catalog, gold_export, quality_contract, quality_intrinsic, quality_rules
from app.services.medallion_stats import list_columns
from app.services.quality_collector import collect_quality
from app.services.quality_notify import decode_config, encode_config, mask_config, notify_alerts, send_digest
from app.services.quality_rules import get_or_create_rule

router = APIRouter(prefix="/api/medallion/projects/{pid}", tags=["quality"])

TREND_POINTS = 10


def _schema_diff(previous_cols: list[dict], latest_cols: list[dict]) -> list[SchemaDiffEntry]:
    prev = {c["column"]: c["type"] for c in previous_cols}
    curr = {c["column"]: c["type"] for c in latest_cols}
    diff: list[SchemaDiffEntry] = []
    for col, t in curr.items():
        if col not in prev:
            diff.append(SchemaDiffEntry(column=col, change="added", new_type=t))
        elif prev[col] != t:
            diff.append(SchemaDiffEntry(column=col, change="retyped", old_type=prev[col], new_type=t))
    for col, t in prev.items():
        if col not in curr:
            diff.append(SchemaDiffEntry(column=col, change="removed", old_type=t))
    return diff


def _dataset_quality(ds: MedallionDataset, snapshots: list[DataQualitySnapshot]) -> DatasetQualityOut:
    # snapshots passed in ascending collected_at order
    trend = snapshots[-TREND_POINTS:]
    latest = trend[-1] if trend else None
    previous = trend[-2] if len(trend) >= 2 else None

    volume_variation_pct = None
    if latest is not None and previous is not None and previous.row_count:
        volume_variation_pct = round((latest.row_count - previous.row_count) / previous.row_count * 100, 1)

    schema_diff: list[SchemaDiffEntry] = []
    if latest is not None and previous is not None and latest.schema_hash != previous.schema_hash:
        schema_diff = _schema_diff(previous.schema_json, latest.schema_json)

    return DatasetQualityOut(
        dataset_id=ds.id,
        dataset_name=ds.name,
        latest=latest,
        trend=trend,
        volume_variation_pct=volume_variation_pct,
        schema_changed=bool(schema_diff),
        schema_diff=schema_diff,
    )


def _get_dataset(db: Session, pid: int, did: int) -> MedallionDataset:
    ds = db.query(MedallionDataset).filter(MedallionDataset.id == did, MedallionDataset.project_id == pid).first()
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset introuvable.")
    return ds


def _resolve_deploy_target(db: Session, project: MedallionProject) -> airflow_instances.DeployTarget:
    try:
        instance = airflow_instances.resolve_project_instance(db, project)
        return airflow_instances.resolve_deploy_target(db, instance, project.dbt_project_name)
    except airflow_instances.AirflowInstanceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/datasets/{did}/quality/history", response_model=list[QualitySnapshotOut])
def get_dataset_quality_history(did: int, limit: int = 30, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    _get_dataset(db, project.id, did)
    return (
        db.query(DataQualitySnapshot)
        .filter(DataQualitySnapshot.dataset_id == did)
        .order_by(DataQualitySnapshot.collected_at.desc())
        .limit(limit)
        .all()[::-1]
    )


@router.post("/quality/collect", response_model=list[QualitySnapshotOut])
async def manual_collect_quality(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    target = _resolve_deploy_target(db, project)
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source warehouse introuvable pour ce projet.")
    return await run_in_threadpool(collect_quality, db, project, datasets, warehouse, target, None, None)


@router.get("/quality/metrics", response_model=list[MetricOut])
def get_quality_metrics(run_id: int | None = None, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 16 §4.4 — intrinsic-quality metrics for one run, or the latest run that has any
    (run_id omitted). Read access, same as the rest of the Qualité tab."""
    if run_id is None:
        latest = (
            db.query(DataQualitySnapshot.run_id)
            .join(DataQualityMetric, DataQualityMetric.snapshot_id == DataQualitySnapshot.id)
            .filter(DataQualitySnapshot.project_id == project.id, DataQualitySnapshot.run_id.isnot(None))
            .order_by(DataQualitySnapshot.collected_at.desc())
            .first()
        )
        run_id = latest[0] if latest else None
    if run_id is None:
        return []
    metrics = (
        db.query(DataQualityMetric)
        .join(DataQualitySnapshot, DataQualityMetric.snapshot_id == DataQualitySnapshot.id)
        .filter(DataQualitySnapshot.project_id == project.id, DataQualitySnapshot.run_id == run_id)
        .order_by(DataQualityMetric.layer, DataQualityMetric.dataset_id, DataQualityMetric.indicator)
        .all()
    )
    return [MetricOut.from_model(m) for m in metrics]


@router.post("/quality/metrics/recompute", response_model=list[MetricOut])
async def recompute_quality_metrics(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Module 16 §4.4 — manual replay (debug/backfill), idempotent: always targets the
    project's latest run (never a bare, unrepeatable "now" like M5's manual /quality/collect),
    so recomputing twice updates the same rows in place rather than accumulating duplicates."""
    run = db.query(MedallionRun).filter(MedallionRun.project_id == project.id, MedallionRun.state.in_([RunState.success, RunState.failed])).order_by(MedallionRun.finished_at.desc()).first()
    if run is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun run terminé pour ce projet.")
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source warehouse introuvable pour ce projet.")
    metrics = await run_in_threadpool(quality_intrinsic.collect, db, project, datasets, warehouse, run)
    # §8.3 — same wiring as the post-run passage in run_status.py: a manual recompute is the
    # only way to see coherence alerts without waiting for/re-running a real pipeline run.
    coherence_alerts = quality_rules.evaluate_coherence(db, project.id, metrics, {d.id: d.name for d in datasets})
    notify_alerts(db, project, coherence_alerts)
    return [MetricOut.from_model(m) for m in metrics]


def _indicator_cell(m: DataQualityMetric) -> OverviewCellOut:
    return OverviewCellOut(target_column=m.target_column or None, defect_rate=m.defect_rate, score=m.score, status=m.status, raw=m.raw)


def _worst(cells: list[OverviewCellOut]) -> OverviewCellOut:
    """§5.2 — the cell that represents its whole indicator column at a glance: the lowest
    score (most defective) among real results; if every underlying check was skipped, the
    indicator itself reads as skipped rather than silently picking an arbitrary one."""
    scored = [c for c in cells if c.score is not None]
    if scored:
        return min(scored, key=lambda c: c.score)
    return cells[0]


def _build_overview(db: Session, project: MedallionProject, run_id: int | None) -> QualityOverviewOut:
    if run_id is None:
        latest = (
            db.query(DataQualitySnapshot.run_id)
            .join(DataQualityMetric, DataQualityMetric.snapshot_id == DataQualitySnapshot.id)
            .filter(DataQualitySnapshot.project_id == project.id, DataQualitySnapshot.run_id.isnot(None))
            .order_by(DataQualitySnapshot.collected_at.desc())
            .first()
        )
        run_id = latest[0] if latest else None
    if run_id is None:
        return QualityOverviewOut(project_id=project.id, run_id=None, layers=[])

    metrics = (
        db.query(DataQualityMetric)
        .join(DataQualitySnapshot, DataQualityMetric.snapshot_id == DataQualitySnapshot.id)
        .filter(DataQualitySnapshot.project_id == project.id, DataQualitySnapshot.run_id == run_id)
        .all()
    )
    names = {d.id: d.name for d in db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()}

    layers_out: list[OverviewLayerOut] = []
    for layer in (QualityLayer.bronze, QualityLayer.silver, QualityLayer.gold):
        layer_metrics = [m for m in metrics if m.layer == layer]
        if not layer_metrics:
            continue

        # composite_score (§3.4) — uniform weights (DataQualityRule.layer_weights doesn't
        # exist yet, that's Étape 5): a flat mean of every real (non-skipped) score at this
        # layer. Never stored — recomputed here at read time.
        scored = [m.score for m in layer_metrics if m.score is not None]
        composite = round(sum(scored) / len(scored), 4) if scored else None

        by_table: dict[int, dict[QualityIndicator, list[DataQualityMetric]]] = {}
        for m in layer_metrics:
            by_table.setdefault(m.dataset_id, {}).setdefault(m.indicator, []).append(m)

        tables_out = []
        for dataset_id, by_indicator in by_table.items():
            indicators_out = []
            for indicator, rows in by_indicator.items():
                cells = [_indicator_cell(m) for m in rows]
                rep = _worst(cells)
                indicators_out.append(OverviewIndicatorOut(indicator=indicator, score=rep.score, defect_rate=rep.defect_rate, status=rep.status, cells=cells))
            tables_out.append(OverviewTableOut(dataset_id=dataset_id, dataset_name=names.get(dataset_id, "?"), indicators=indicators_out))
        tables_out.sort(key=lambda t: t.dataset_name)

        layers_out.append(OverviewLayerOut(layer=layer, composite_score=composite, tables=tables_out))

    return QualityOverviewOut(project_id=project.id, run_id=run_id, layers=layers_out)


@router.get("/quality/overview", response_model=QualityOverviewOut)
def get_quality_overview(run_id: int | None = None, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 16 §5.3 — the pipeline × layer matrix, latest run by default."""
    return _build_overview(db, project, run_id)


def _baseline_out(baseline: DataQualityBaseline | None, project_id: int) -> BaselineOut:
    if baseline is None:
        return BaselineOut(project_id=project_id, assertions=BaselineAssertions())
    return BaselineOut(project_id=project_id, assertions=BaselineAssertions.model_validate(baseline.assertions), updated_at=baseline.updated_at, updated_by=baseline.updated_by)


@router.get("/quality/baseline", response_model=BaselineOut)
def get_quality_baseline(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 16 §6.4 — read access matches the rest of the tab (engineer); a project with no
    baseline yet returns an all-empty draft rather than 404, since "no baseline" is the normal
    starting state, not an error."""
    baseline = db.query(DataQualityBaseline).filter(DataQualityBaseline.project_id == project.id).first()
    return _baseline_out(baseline, project.id)


@router.put("/quality/baseline", response_model=BaselineOut)
def update_quality_baseline(payload: BaselineUpdate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user)):
    """Module 16 §6.4/§6.5 — the only write path; ownership-governed, same as everywhere else
    in this module. Also how "mettre à jour la baseline" (the deviation double-path, §6.5)
    saves — no separate endpoint, it's the same PUT with an edited assertion."""
    baseline = db.query(DataQualityBaseline).filter(DataQualityBaseline.project_id == project.id).first()
    assertions_dict = payload.assertions.model_dump(mode="json")
    if baseline is None:
        baseline = DataQualityBaseline(project_id=project.id, assertions=assertions_dict, updated_by=current_user.id)
        db.add(baseline)
    else:
        baseline.assertions = assertions_dict
        baseline.updated_by = current_user.id
    db.commit()
    db.refresh(baseline)
    return _baseline_out(baseline, project.id)


@router.post("/quality/baseline/suggest", response_model=BaselineSuggestionOut)
async def suggest_quality_baseline(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Module 16 §6.3/§6.4 — non-persistent AI draft; the engineer reviews then PUTs their own
    (possibly edited) version separately."""
    return await quality_contract.suggest_baseline(db, project)


@router.post("/quality/checks/suggest", response_model=CheckSuggestOut)
async def suggest_quality_checks(dataset_id: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Module 16 §7.4 — non-persistent AI draft for ONE dataset; the engineer reviews then
    POSTs their own selection via /quality/checks separately (same two-step pattern as the
    baseline draft above)."""
    dataset = _get_dataset(db, project.id, dataset_id)
    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source warehouse introuvable pour ce projet.")
    return await quality_contract.suggest_checks(db, dataset, warehouse)


@router.get("/quality/checks", response_model=list[CheckOut])
def list_quality_checks(dataset_id: int | None = None, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 16 §7.4 — every check regardless of status (proposed/active/dismissed): the
    « Contrôles » screen itself decides what to show where (§7.5)."""
    q = db.query(DataQualityCheck).filter(DataQualityCheck.project_id == project.id)
    if dataset_id is not None:
        q = q.filter(DataQualityCheck.dataset_id == dataset_id)
    checks = q.order_by(DataQualityCheck.created_at.desc()).all()
    return [CheckOut.from_model(c) for c in checks]


@router.post("/quality/checks", response_model=CheckOut, status_code=status.HTTP_201_CREATED)
def create_quality_check(payload: CheckCreate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Module 16 §7.4/§7.6.1/§7.6.4 — validating an AI suggestion (source=ai_suggested) and
    authoring a manual check (source=engineer) both land here, both held to the exact same
    validate_check_parameters boundary — a manual check gets no free pass sql_validator would
    have rejected from the AI."""
    dataset = _get_dataset(db, project.id, payload.dataset_id)
    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source warehouse introuvable pour ce projet.")
    real_columns = {c["column"] for c in list_columns(warehouse, dataset)}
    errors = quality_contract.validate_check_parameters(payload.check_type, payload.target_column, payload.parameters, real_columns)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ; ".join(errors))
    check = DataQualityCheck(
        project_id=project.id, dataset_id=dataset.id, layer=payload.layer, target_column=payload.target_column,
        check_type=payload.check_type, parameters=payload.parameters, source=payload.source, status=CheckStatus.active,
    )
    db.add(check)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un contrôle identique existe déjà pour ce dataset.")
    db.refresh(check)
    return CheckOut.from_model(check)


def _get_check(db: Session, pid: int, check_id: int) -> DataQualityCheck:
    check = db.query(DataQualityCheck).filter(DataQualityCheck.id == check_id, DataQualityCheck.project_id == pid).first()
    if check is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrôle introuvable.")
    return check


@router.put("/quality/checks/{check_id}", response_model=CheckOut)
def update_quality_check(check_id: int, payload: CheckUpdate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Module 16 §7.4/§7.5 — activer/désactiver (status) and/or modifier ses paramètres; a
    parameters/target_column edit is re-validated exactly like a fresh creation (§7.6.4 doesn't
    carve out an exception for edits)."""
    check = _get_check(db, project.id, check_id)
    new_target_column = payload.target_column if payload.target_column is not None else check.target_column
    new_parameters = payload.parameters if payload.parameters is not None else check.parameters
    if payload.parameters is not None or payload.target_column is not None:
        dataset = _get_dataset(db, project.id, check.dataset_id) if check.dataset_id else None
        warehouse = db.get(DataSource, project.warehouse_source_id)
        real_columns = {c["column"] for c in list_columns(warehouse, dataset)} if dataset and warehouse else set()
        errors = quality_contract.validate_check_parameters(check.check_type, new_target_column, new_parameters, real_columns)
        if errors:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ; ".join(errors))
        check.target_column = new_target_column
        check.parameters = new_parameters
    if payload.status is not None:
        check.status = payload.status
    # Module 16 extension §5 — tri-state control: materialize_as_dbt_test can only ever be
    # True for a check_type the catalogue actually knows how to render (§5 "familles non
    # supportées : contrôle grisé") — grayed out client-side, re-checked server-side too.
    if payload.materialize_as_dbt_test is not None:
        if payload.materialize_as_dbt_test and not expectation_catalog.is_materializable(check.check_type):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce type de contrôle ne peut pas être matérialisé en test dbt (observation seule).")
        check.materialize_as_dbt_test = payload.materialize_as_dbt_test
    if payload.dbt_test_severity is not None:
        check.dbt_test_severity = payload.dbt_test_severity
    db.commit()
    db.refresh(check)
    return CheckOut.from_model(check)


@router.delete("/quality/checks/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quality_check(check_id: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    check = _get_check(db, project.id, check_id)
    db.delete(check)
    db.commit()
    return None


@router.get("/quality", response_model=ProjectQualityOut)
def get_project_quality(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    pid = project.id
    gold_datasets = (
        db.query(MedallionDataset)
        .filter(MedallionDataset.project_id == pid, MedallionDataset.layer == MedallionLayer.gold)
        .all()
    )
    if not gold_datasets:
        return ProjectQualityOut(project_id=pid, datasets=[])

    all_snapshots = (
        db.query(DataQualitySnapshot)
        .filter(DataQualitySnapshot.dataset_id.in_([d.id for d in gold_datasets]))
        .order_by(DataQualitySnapshot.collected_at.asc())
        .all()
    )
    by_dataset: dict[int, list[DataQualitySnapshot]] = {d.id: [] for d in gold_datasets}
    for snap in all_snapshots:
        by_dataset[snap.dataset_id].append(snap)

    return ProjectQualityOut(
        project_id=pid,
        datasets=[_dataset_quality(ds, by_dataset[ds.id]) for ds in gold_datasets],
    )


@router.get("/quality/rules", response_model=RuleOut)
def get_quality_rules(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    return get_or_create_rule(db, project.id)


@router.put("/quality/rules", response_model=RuleOut)
def update_quality_rules(payload: RuleUpdate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    rule = get_or_create_rule(db, project.id)
    for field, value in payload.model_dump().items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/quality/alerts", response_model=list[AlertOut])
def list_quality_alerts(status_filter: AlertStatus | None = Query(default=None, alias="status"), db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    q = db.query(DataQualityAlert).filter(DataQualityAlert.project_id == project.id)
    if status_filter is not None:
        q = q.filter(DataQualityAlert.status == status_filter)
    return q.order_by(DataQualityAlert.created_at.desc()).all()


@router.post("/quality/alerts/{alert_id}/ack", response_model=AlertOut)
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    alert = db.query(DataQualityAlert).filter(DataQualityAlert.id == alert_id, DataQualityAlert.project_id == project.id).first()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerte introuvable.")
    alert.status = AlertStatus.acknowledged
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/quality/alerts/{alert_id}/explain", response_model=AlertOut)
async def explain_quality_alert(alert_id: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 16 §8.3/§8.4 — read access matches the rest of the tab (engineer, not just the
    owner): explaining an alert doesn't change any state an owner should gate. Idempotent (§2)
    — an already-cached explanation is returned as-is, never re-calling the AI."""
    alert = db.query(DataQualityAlert).filter(DataQualityAlert.id == alert_id, DataQualityAlert.project_id == project.id).first()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerte introuvable.")
    if alert.explanation is None:
        alert.explanation = await quality_contract.explain_alert(alert)
        db.commit()
        db.refresh(alert)
    return alert


def _channel_out(channel: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=channel.id, project_id=channel.project_id, type=channel.type,
        config=mask_config(channel.type, decode_config(channel)),
        enabled=channel.enabled, min_severity=channel.min_severity, created_at=channel.created_at,
    )


def _get_channel(db: Session, pid: int, channel_id: int) -> NotificationChannel:
    channel = db.query(NotificationChannel).filter(NotificationChannel.id == channel_id, NotificationChannel.project_id == pid).first()
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canal introuvable.")
    return channel


@router.get("/quality/channels", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    channels = db.query(NotificationChannel).filter(NotificationChannel.project_id == project.id).order_by(NotificationChannel.created_at.desc()).all()
    return [_channel_out(c) for c in channels]


# Notification channels are platform-wide admin infrastructure (Slack/email endpoints),
# not part of "the pipeline definition" — deliberately exempt from ownership (any admin
# configures them on any project), unlike everything else in this module.
@router.post("/quality/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
def create_channel(payload: ChannelCreate, db: Session = Depends(get_db), _: User = Depends(require_admin), project: MedallionProject = Depends(get_readable_project)):
    channel = NotificationChannel(
        project_id=project.id, type=payload.type, config_encrypted=encode_config(payload.config),
        enabled=payload.enabled, min_severity=payload.min_severity,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return _channel_out(channel)


@router.put("/quality/channels/{channel_id}", response_model=ChannelOut)
def update_channel(channel_id: int, payload: ChannelUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin), project: MedallionProject = Depends(get_readable_project)):
    channel = _get_channel(db, project.id, channel_id)
    if payload.config is not None:
        channel.config_encrypted = encode_config(payload.config)
    if payload.enabled is not None:
        channel.enabled = payload.enabled
    if payload.min_severity is not None:
        channel.min_severity = payload.min_severity
    db.commit()
    db.refresh(channel)
    return _channel_out(channel)


@router.delete("/quality/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(channel_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin), project: MedallionProject = Depends(get_readable_project)):
    channel = _get_channel(db, project.id, channel_id)
    db.delete(channel)
    db.commit()


@router.post("/quality/channels/{channel_id}/test")
def test_channel(channel_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin), project: MedallionProject = Depends(get_readable_project)):
    channel = _get_channel(db, project.id, channel_id)
    fake_alert = DataQualityAlert(
        id=0, dataset_id=0, project_id=project.id, snapshot_id=0,
        type=AlertType.volume, severity=AlertSeverity.critical,
        message="Ceci est un message de test envoyé depuis Data Plateforme.",
        status=AlertStatus.open, created_at=datetime.now(timezone.utc),
    )
    try:
        send_digest(channel, project, [fake_alert])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Envoi impossible : {exc}")
    return {"sent": True}


# ---------------- Export dbt autoportant (Module 16 extension §6) ----------------

@router.get("/quality/export/dbt/preview")
def preview_dbt_export(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Inventory only (§6 frontend) — no file built, same permission bar as the download
    itself (owner, like the CSV export §11-extension precedent: an egress-shaped action)."""
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    return {"inventory": dbt_export.inventory_preview(db, project.id, datasets)}


@router.get("/quality/export/dbt")
def export_dbt_project(
    request: Request, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
):
    """§6 — a standalone, runnable-off-platform dbt project archive: `dbt_test_renderer`
    replayed for every active check (not just the ones opted into live enforcement, §6) then
    serialized (§6 "l'export ne fabrique rien de neuf"). Owner only, 404 otherwise (M9)."""
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    bronze_ids = [d.id for d in datasets if d.layer == MedallionLayer.bronze]
    structurations = (
        {s.dataset_id: s for s in db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id.in_(bronze_ids)).all()}
        if bronze_ids else {}
    )
    zip_bytes, _inventory = dbt_export.build_export(db, project, datasets, structurations)
    gold_export.log_export(db, project.id, None, current_user.id, gold_export.client_ip_of(request), kind=ExportKind.dbt_project)
    filename = f"{project.dbt_project_name}_dbt_export.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
