import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.deps_medallion import get_owned_project, get_project_binding, get_readable_project
from app.db.session import get_db
from app.models.airflow_instance import AirflowInstance, AirflowInstanceOrigin
from app.models.data_source import DataSource, DataSourceType
from app.models.dbt_macro import DbtMacro
from app.models.export_log import ExportLog
from app.models.file_import import FileImport, FileImportStatus, FileImportWriteMode, ImportMode
from app.models.file_watch import FileWatch
from app.models.infra_stack import InfraStack
from app.models.payload_structuration import PayloadStructuration
from app.models.project_environment_binding import ProjectEnvironmentBinding
from app.models.server import Environment, Server
from app.models.medallion import (
    MedallionDataset,
    MedallionFolder,
    MedallionLayer,
    MedallionProject,
    MedallionRun,
    MedallionVersion,
    ProjectStatus,
    RunState,
    TransformType,
    WorkspaceParseStatus,
)
from app.models.user import User
from app.models.dashboard_spec import DashboardSpec
from app.models.pipeline_plan import PipelinePlan
from app.models.superset_instance import SupersetInstance
from app.models.superset_publication import SupersetPublication
from app.schemas.medallion import (
    BuildReport,
    BuildRequest,
    DatasetColumnOut,
    DatasetColumnsOut,
    DatasetCreate,
    DataSampleColumnOut,
    DataSampleOut,
    DataSampleTargetOut,
    DatasetOut,
    DatasetUpdate,
    DeployStatusOut,
    DatasetPublicationOut,
    ExportLogOut,
    LineageEdge,
    LineageGraph,
    LineageNode,
    PreviewResult,
    PublicationStatusOut,
    DashboardStatusOut,
    DashboardSummaryOut,
    GenerateDashboardOut,
    GenerateDashboardRequest,
    ProfiledColumnOut,
    PublishRequest,
    PublishResultOut,
    ProjectCreate,
    ProjectFolderUpdate,
    ProjectOut,
    ProjectUpdate,
    RestoreRequest,
    RunOut,
    RunTriggerRequest,
    SqlValidationOut,
    SqlValidationRequest,
    SuggestedIndicatorOut,
    SuggestIndicatorsOut,
    VersionDetailOut,
    VersionDiffOut,
    VersionOut,
)
from app.schemas.environment_binding import BindingOut
from app.schemas.promotion import (
    AlertBriefOut,
    DiffOut,
    ProdBindingUpsert,
    PromoteRequest,
    PromotionPreviewOut,
    SourceMappingConfirm,
    SourceMappingOut,
)
from app.schemas.payload_structuration import (
    StructurationOut,
    StructurationUpdate,
)
from app.schemas.file_import import ColumnsOut, FileImportOut, ImportFromObjectStoreCreate, ObjectStoreColumnsRequest
from app.services import ai_client, airflow_api, airflow_instances, dag_render, dbt_macros, dbt_project, file_import as file_import_service, gold_export, gold_profile, impact, indicator_suggest, payload_structure, preview, promotion, schedule, superset_publish, version_diff, version_restore, version_snapshot, workspace, workspace_merge, workspace_sync
from app.services.ai_config import get_ai_config
from app.services.superset_instances import get_superset_config
from app.services.medallion_crud import create_dataset_internal, validate_lineage
from app.services.medallion_deploy import build_project
from app.services.medallion_stats import build_ref_map, list_columns
from app.services import run_status

logger = logging.getLogger("app.medallion")

router = APIRouter(prefix="/api/medallion/projects", tags=["medallion"])


def _get_dataset(db: Session, pid: int, did: int) -> MedallionDataset:
    ds = db.query(MedallionDataset).filter(MedallionDataset.id == did, MedallionDataset.project_id == pid).first()
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset introuvable.")
    return ds


def _resolve_airflow_config(db: Session, project: MedallionProject) -> airflow_instances.AirflowConfig:
    try:
        instance = airflow_instances.resolve_project_instance(db, project)
        return airflow_instances.get_airflow_config(instance)
    except airflow_instances.AirflowInstanceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _resolve_deploy_target(db: Session, project: MedallionProject) -> airflow_instances.DeployTarget:
    try:
        instance = airflow_instances.resolve_project_instance(db, project)
        return airflow_instances.resolve_deploy_target(db, instance, project.dbt_project_name)
    except airflow_instances.AirflowInstanceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _environment_of_instance(db: Session, airflow_instance_id: int) -> Environment:
    """Module 17 §0/§3.1 — a binding's environment is derived from the server hosting its
    Airflow instance ("un serveur = un environnement"). A platform instance traces through
    its InfraStack to a tagged Server; an external instance has no server to derive from and
    defaults to dev (same reasoning as the migration's own backfill default)."""
    instance = db.get(AirflowInstance, airflow_instance_id)
    if instance is not None and instance.origin == AirflowInstanceOrigin.platform and instance.stack_id:
        stack = db.get(InfraStack, instance.stack_id)
        if stack is not None:
            server = db.get(Server, stack.server_id)
            if server is not None:
                return server.environment
    return Environment.dev


# ---------------- Projects ----------------

@router.get("/", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # "Médaillons" is always *my* projects — for every role, admin included (§3.1). Admin
    # supervision across everyone else's projects lives under /admin/overview instead.
    return (
        db.query(MedallionProject)
        .filter(MedallionProject.owner_id == current_user.id)
        .order_by(MedallionProject.created_at.asc())
        .all()
    )


@router.post("/", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    for source_id in (payload.object_store_source_id, payload.warehouse_source_id):
        if db.get(DataSource, source_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source introuvable.")
    if db.get(AirflowInstance, payload.airflow_instance_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Instance Airflow introuvable.")
    if payload.superset_instance_id is not None and db.get(SupersetInstance, payload.superset_instance_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Instance Superset introuvable.")
    if payload.schedule:
        try:
            schedule.validate_schedule(payload.schedule)
        except schedule.ScheduleError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Module 17 — the payload's infra fields (warehouse/object store/airflow instance/
    # schedule/target) no longer live on MedallionProject; they become the project's home
    # binding, created in the same transaction. owner_id is never accepted from the client —
    # always the creator.
    fields = payload.model_dump()
    binding_fields = {
        k: fields.pop(k) for k in ("object_store_source_id", "warehouse_source_id", "airflow_instance_id", "schedule", "target")
    }
    project = MedallionProject(**fields, owner_id=current_user.id, has_pending_changes=False)
    db.add(project)
    db.flush()  # assigns project.id, needed for the binding's FK, before either is committed

    environment = _environment_of_instance(db, binding_fields["airflow_instance_id"])
    home_binding = ProjectEnvironmentBinding(
        project_id=project.id, environment=environment, is_home=True, status=ProjectStatus.draft, **binding_fields,
    )
    db.add(home_binding)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{pid}", response_model=ProjectOut)
def get_project(project: MedallionProject = Depends(get_readable_project)):
    return project


@router.put("/{pid}", response_model=ProjectOut)
def update_project(payload: ProjectUpdate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("superset_instance_id") is not None and db.get(SupersetInstance, fields["superset_instance_id"]) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Instance Superset introuvable.")
    if fields.get("schedule"):
        try:
            schedule.validate_schedule(fields["schedule"])
        except schedule.ScheduleError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    for field, value in fields.items():
        setattr(project, field, value)
    if project.status in (ProjectStatus.deployed, ProjectStatus.paused):
        # A paused project still has a real, live DAG in Airflow (just not currently
        # triggering) — an edit staled it exactly as much as it would a running one. Missing
        # this case meant editing anything on a paused project never showed "à redéployer".
        project.has_pending_changes = True
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    db.delete(project)
    db.commit()


@router.patch("/{pid}/folder", response_model=ProjectOut)
def move_project_folder(payload: ProjectFolderUpdate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Module 15 §3.3 — the ONLY write path for folder_id (PUT /projects/{pid} deliberately
    ignores it, see ProjectUpdate's schema). Purely organizational: no other field on the
    project changes, no build/run/pause/redeploy side effect."""
    if payload.folder_id is not None:
        # get_owned_folder is a FastAPI dependency (Depends(...)), not a plain function — call
        # it directly here since this needs to happen conditionally, not on every request.
        folder = db.get(MedallionFolder, payload.folder_id)
        if folder is None or folder.owner_id != project.owner_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier introuvable.")
    project.folder_id = payload.folder_id
    db.commit()
    db.refresh(project)
    return project


# ---------------- Datasets ----------------

@router.get("/{pid}/datasets", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).order_by(MedallionDataset.created_at.asc()).all()
    payload_backed = payload_structure.bulk_payload_backed(db, datasets)
    code_modified = workspace.bulk_code_modified(db, [d.id for d in datasets])
    for d in datasets:
        d.payload_backed = payload_backed.get(d.id, False)
        d.code_modified = code_modified.get(d.id, False)
    return datasets


@router.post("/{pid}/datasets", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
def create_dataset(payload: DatasetCreate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    # Bronze for a file-import-backed dataset is mirrored synchronously at import time now
    # (file_import.py's _sync_bronze_mirror) — nothing left to do for it here.
    dataset = create_dataset_internal(db, project, payload)
    db.commit()
    db.refresh(dataset)
    dataset.payload_backed = payload_structure.resolve_import(db, dataset) is not None
    dataset.code_modified = False  # freshly created through the canvas — no linked file yet
    return dataset


@router.post("/{pid}/import-from-object-store/columns", response_model=ColumnsOut)
def object_store_columns(
    payload: ObjectStoreColumnsRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project),
):
    """Scratch analysis only — no FileImport row, nothing archived. Mirrors POST
    /api/imports/columns (the standalone wizard's own source_pk candidate list) so a CSV/Excel
    file already sitting in a bucket gets the exact same composite-key picker, not just a
    free-text field, before the engineer commits to processing it."""
    source = db.get(DataSource, payload.source_id)
    if source is None or source.type != DataSourceType.minio:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source MinIO/S3 introuvable.")
    try:
        file_bytes = file_import_service.fetch_object_bytes(source, payload.bucket, payload.key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Lecture du fichier « {payload.bucket}/{payload.key} » impossible : {exc}")
    try:
        columns, _rows = file_import_service.read_columns_and_sample(payload.format, file_bytes, payload.format_options)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Analyse des colonnes impossible : {exc}")
    return ColumnsOut(columns=columns)


@router.post("/{pid}/import-from-object-store", response_model=FileImportOut, status_code=status.HTTP_201_CREATED)
def import_from_object_store(
    payload: ImportFromObjectStoreCreate, db: Session = Depends(get_db),
    project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user),
):
    """UX ask — the same payload/typed choice the standalone Imports wizard offers for an
    uploaded file, applied to a CSV/Excel file already sitting in an S3/MinIO bucket, browsed
    from right here in the medallion canvas's own bronze-dataset picker (DatasetPanel). The
    project's own warehouse is always the target — this route only exists inside a project's
    context, unlike the standalone wizard, which has no project to default to — and the same
    MinIO source both supplies and archives the bytes, so there's nothing left to pick. Creating
    the resulting MedallionDataset (pointing at this import's `<target_schema>.<target_table>`)
    is still its own, separate, explicit step — exactly like the standalone Imports flow today
    (create the import, then add a bronze dataset pointing at it), not something this route
    does automatically."""
    source = db.get(DataSource, payload.source_id)
    if source is None or source.type != DataSourceType.minio:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source MinIO/S3 introuvable.")
    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Warehouse du projet introuvable.")

    try:
        file_bytes = file_import_service.fetch_object_bytes(source, payload.bucket, payload.key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Lecture du fichier « {payload.bucket}/{payload.key} » impossible : {exc}")

    file_name = payload.key.rsplit("/", 1)[-1]
    try:
        return file_import_service.create_import_from_bytes(
            db, current_user.id, file_bytes, file_name, payload.format, payload.format_options,
            warehouse, source, payload.name, ImportMode(payload.import_mode), FileImportWriteMode(payload.write_mode),
        )
    except file_import_service.ArchiveError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except file_import_service.FileImportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.put("/{pid}/datasets/{did}", response_model=DatasetOut)
def update_dataset(did: int, payload: DatasetUpdate, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    dataset = _get_dataset(db, project.id, did)

    data = payload.model_dump(exclude_unset=True)
    if "upstream_dataset_ids" in data:
        transform_type = data.get("transform_type", dataset.transform_type)
        validate_lineage(db, project.id, dataset.layer, data["upstream_dataset_ids"], transform_type)
    for field, value in data.items():
        setattr(dataset, field, value)

    if project.status in (ProjectStatus.deployed, ProjectStatus.paused):
        # A paused project still has a real, live DAG in Airflow (just not currently
        # triggering) — an edit staled it exactly as much as it would a running one. Missing
        # this case meant editing anything on a paused project never showed "à redéployer".
        project.has_pending_changes = True
    db.commit()
    db.refresh(dataset)
    dataset.payload_backed = payload_structure.resolve_import(db, dataset) is not None
    dataset.code_modified = workspace.bulk_code_modified(db, [dataset.id]).get(dataset.id, False)
    return dataset


@router.delete("/{pid}/datasets/{did}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    dataset = _get_dataset(db, project.id, did)
    db.delete(dataset)
    if project.status in (ProjectStatus.deployed, ProjectStatus.paused):
        # A paused project still has a real, live DAG in Airflow (just not currently
        # triggering) — an edit staled it exactly as much as it would a running one. Missing
        # this case meant editing anything on a paused project never showed "à redéployer".
        project.has_pending_changes = True
    db.commit()


@router.get("/{pid}/datasets/{did}/columns", response_model=DatasetColumnsOut)
def get_dataset_columns(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Live column list read straight from the warehouse — helps authoring dbt SQL for
    datasets upstream of the one being edited. Empty (not an error) if that dataset's
    table hasn't been created yet (no successful pipeline run so far).

    A payload-mode bronze dataset's own physical table is just `payload`/`load_id`/
    `source_file`/`row_number`/`source_pk`/`source_system`/`loaded_at` (§3.3 audit shape) —
    useless for authoring downstream SQL. Once it has a saved structuration contract, its
    `02_typed_<name>` model (the guaranteed, always-rendered stage; 03_standardized onward are
    optional hand-written datasets, never assumed to exist) is what silver/gold should
    reference instead, so its *structured* columns (the profiled, included
    target_name/target_type pairs) are returned here in its place."""
    dataset = _get_dataset(db, project.id, did)
    if dataset.layer == MedallionLayer.bronze:
        structuration = db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id == dataset.id).first()
        if structuration is not None and structuration.contract_hash:
            included = [f for f in structuration.column_mapping if f.get("include", True)]
            columns = [DatasetColumnOut(column=f["target_name"], type=f["target_type"]) for f in included]
            return DatasetColumnsOut(columns=columns, table_exists=bool(columns), structured=True)

    warehouse = db.get(DataSource, project.warehouse_source_id)
    if not warehouse:
        return DatasetColumnsOut(columns=[], table_exists=False)
    columns = list_columns(warehouse, dataset)
    return DatasetColumnsOut(columns=[DatasetColumnOut(**c) for c in columns], table_exists=bool(columns))


@router.get("/{pid}/datasets/{did}/preview", response_model=DataSampleOut)
def get_dataset_preview(
    did: int, limit: int = preview.PREVIEW_DEFAULT_LIMIT, offset: int = 0, source: bool = False,
    db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project),
):
    """Module 10 — bounded, read-only sample of a dataset's real table (never SQL from the
    client; identifiers reflected/quoted by SQLAlchemy). `source=true` forces the upstream
    source table instead of the materialized output — used for the canvas's origin nodes."""
    dataset = _get_dataset(db, project.id, did)
    outcome = preview.resolve_and_sample(db, project, dataset, limit=limit, offset=offset, prefer_source=source)
    return DataSampleOut(
        status=outcome.status,
        message=outcome.message,
        columns=[DataSampleColumnOut(**c) for c in (outcome.columns or [])],
        rows=outcome.rows or [],
        truncated=outcome.truncated,
        target=DataSampleTargetOut(kind=outcome.target.kind, schema_name=outcome.target.schema_name, table=outcome.target.table, source_name=outcome.target.source_name) if outcome.target else None,
    )


@router.get("/{pid}/datasets/{did}/structuration/preview", response_model=DataSampleOut)
def preview_structuration_silver_table(
    did: int, stage: str, limit: int = preview.PREVIEW_DEFAULT_LIMIT, offset: int = 0,
    db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project),
):
    """UX ask — silver.01_unpacked_<name>/silver.02_typed_<name> (materialize_unpacked_typed_sync)
    aren't MedallionDataset rows, so get_dataset_preview can't resolve them. Reuses the exact
    same low-level sampler (preview.attempt_sample) against a schema+table derived server-side
    from the bronze dataset's own name — never a client-supplied table string — restricted to
    the two literal stages that mechanism ever creates."""
    stage_prefix = {"unpacked": "01_unpacked", "typed": "02_typed"}.get(stage)
    if stage_prefix is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Étape invalide.")
    dataset = _get_dataset(db, project.id, did)
    warehouse = db.get(DataSource, project.warehouse_source_id)
    limit = max(1, min(limit, preview.PREVIEW_MAX_ROWS))
    outcome = preview.attempt_sample(warehouse, "silver", f"{stage_prefix}_{dataset.name}", "materialized", limit, offset) if warehouse else None
    if outcome is None:
        outcome = (
            preview.PreviewOutcome(status="not_found", message="Aucun warehouse configuré pour ce projet.")
            if warehouse is None else preview.PreviewOutcome(status="not_materialized")
        )
    return DataSampleOut(
        status=outcome.status, message=outcome.message,
        columns=[DataSampleColumnOut(**c) for c in (outcome.columns or [])],
        rows=outcome.rows or [], truncated=outcome.truncated,
        target=DataSampleTargetOut(kind=outcome.target.kind, schema_name=outcome.target.schema_name, table=outcome.target.table, source_name=outcome.target.source_name) if outcome.target else None,
    )


def _structuration_out(row: PayloadStructuration) -> StructurationOut:
    return StructurationOut(
        dataset_id=row.dataset_id, payload_column=row.payload_column, column_mapping=row.column_mapping,
        contract_hash=row.contract_hash, updated_at=row.updated_at, updated_by=row.updated_by,
    )


@router.post("/{pid}/datasets/{did}/structuration/profile", response_model=StructurationOut)
def profile_dataset_structuration(
    did: int, db: Session = Depends(get_db),
    project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user),
):
    """Module 6 extension (payload & structuration) §4.3/§4.4 — (re)profiles the payload,
    reusing schema_infer verbatim, and persists the proposal as an editable contract (upsert).
    Owner-only: like publish/export, this writes a contract."""
    dataset = _get_dataset(db, project.id, did)
    try:
        mapping = payload_structure.profile_payload(db, dataset)
    except payload_structure.PayloadStructureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    row = db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id == dataset.id).first()
    if row is None:
        row = PayloadStructuration(dataset_id=dataset.id, column_mapping=mapping, updated_by=current_user.id)
        db.add(row)
    else:
        row.column_mapping = mapping
        row.updated_by = current_user.id
        row.contract_hash = None  # a fresh proposal isn't the validated contract until PUT
    db.commit()
    db.refresh(row)
    return _structuration_out(row)


@router.get("/{pid}/datasets/{did}/structuration", response_model=StructurationOut)
def get_dataset_structuration(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    dataset = _get_dataset(db, project.id, did)
    row = db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id == dataset.id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun contrat de structuration pour ce dataset — profilez-le d'abord.")
    return _structuration_out(row)


@router.put("/{pid}/datasets/{did}/structuration", response_model=StructurationOut)
def update_dataset_structuration(
    did: int, payload: StructurationUpdate, db: Session = Depends(get_db),
    project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user),
):
    """§4.4 — persists the validated contract: identifiers checked before anything is written.
    Marks the project as needing a redeploy (a changed contract re-renders the
    01_unpacked/02_typed dbt models at next build) — but doesn't leave the canvas empty until
    that build runs: every save also creates the empty shape of
    silver.01_unpacked_<name>/silver.02_typed_<name> synchronously, right here (see
    materialize_unpacked_typed_sync), so a failure there fails the save too. Rows land later,
    the normal way — dbt_run_silver building the passthrough models the redeploy just rendered."""
    dataset = _get_dataset(db, project.id, did)
    if payload_structure.resolve_import(db, dataset) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce dataset bronze n'est pas adossé à un import en mode payload.")

    column_mapping = [f.model_dump() for f in payload.column_mapping]
    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Warehouse introuvable.")
    try:
        payload_structure.validate_column_mapping(column_mapping)
        payload_structure.render_unpacked_typed_models(column_mapping, dataset.name)
        # UX ask — don't just prove it renders: create the empty shape of
        # silver.01_unpacked_<name>/silver.02_typed_<name> right now, so the canvas has
        # something real to show immediately. A save only succeeds if this actually works;
        # the rows themselves are injected later, by Airflow running the project's DAG.
        payload_structure.materialize_unpacked_typed_sync(warehouse, column_mapping, dataset.name)
    except payload_structure.PayloadStructureError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    row = db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id == dataset.id).first()
    if row is None:
        row = PayloadStructuration(dataset_id=dataset.id)
        db.add(row)
    row.column_mapping = column_mapping
    row.contract_hash = payload_structure.canonical_contract(column_mapping)
    row.updated_by = current_user.id

    if project.status in (ProjectStatus.deployed, ProjectStatus.paused):
        project.has_pending_changes = True
    db.commit()
    db.refresh(row)
    return _structuration_out(row)


@router.get("/{pid}/datasets/{did}/export.csv")
def export_dataset_csv(
    did: int, request: Request,
    db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
):
    """Module 11 extension — stream a whole gold table to the caller as CSV, straight from
    `COPY … TO STDOUT` (never buffered/written/logged by the control plane — only the export
    metadata is). Gold only, owner only: both denials are 404 (never reveal the resource).
    Write-family action (data egress), so `get_owned_project` like publish — not the
    admin-readable path of the M10 preview."""
    dataset = _get_dataset(db, project.id, did)
    try:
        plan = gold_export.preflight(db, project, dataset)
    except gold_export.GoldExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    filename = gold_export.filename_for(dataset)
    export_log_id = gold_export.log_export(
        db, project.id, dataset.id, current_user.id, gold_export.client_ip_of(request),
    )
    stream = gold_export.stream_csv(
        plan, on_complete=lambda n: gold_export.set_export_row_count(export_log_id, n),
    )
    return StreamingResponse(
        stream,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{pid}/exports", response_model=list[ExportLogOut])
def list_project_exports(limit: int = 20, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 11 extension — recent export journal for a project (who exported which gold
    table, when). Readable path: owner, or an admin supervising."""
    limit = max(1, min(limit, 100))
    rows = (
        db.query(ExportLog)
        .filter(ExportLog.project_id == project.id)
        .order_by(ExportLog.exported_at.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return []
    ds_names = {
        d.id: d.name
        for d in db.query(MedallionDataset.id, MedallionDataset.name).filter(MedallionDataset.project_id == project.id).all()
    }
    user_ids = {r.exported_by for r in rows if r.exported_by is not None}
    user_names = (
        {u.id: u.name for u in db.query(User.id, User.name).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    )
    return [
        ExportLogOut(
            id=r.id, dataset_id=r.dataset_id, dataset_name=ds_names.get(r.dataset_id),
            kind=r.kind.value, exported_at=r.exported_at,
            exported_by=user_names.get(r.exported_by), row_count=r.row_count, client_ip=r.client_ip,
        )
        for r in rows
    ]


@router.post("/{pid}/datasets/{did}/publish", response_model=PublishResultOut)
async def publish_dataset_endpoint(
    did: int, payload: PublishRequest = PublishRequest(),
    db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project),
):
    """Module 11 — publish a materialized gold dataset into Superset. Write, owner-only
    (§2/§4.3): even an admin browsing a project read-only cannot publish someone else's data."""
    dataset = _get_dataset(db, project.id, did)
    instance = None
    if payload.superset_instance_id is not None:
        instance = db.get(SupersetInstance, payload.superset_instance_id)
        if instance is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Instance Superset introuvable.")
        # Same "chosen once, reused after" principle as airflow_instance_id — except deferred:
        # a project can be created without one and get it attached right here, at first
        # publish, instead of being forced to pick during creation.
        if project.superset_instance_id != instance.id:
            project.superset_instance_id = instance.id
            db.commit()
    outcome = await superset_publish.publish_dataset(db, project, dataset, published_by_id=project.owner_id, instance=instance)
    return PublishResultOut(status=outcome.status, message=outcome.message, url=outcome.url, superset_dataset_id=outcome.superset_dataset_id, columns_count=outcome.columns_count)


@router.get("/{pid}/datasets/{did}/publication", response_model=PublicationStatusOut)
def get_dataset_publication(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    dataset = _get_dataset(db, project.id, did)
    pub = db.query(SupersetPublication).filter(SupersetPublication.medallion_dataset_id == dataset.id).first()
    if pub is None:
        return PublicationStatusOut(published=False)
    publisher = db.get(User, pub.published_by)
    return PublicationStatusOut(published=True, url=pub.published_url, last_published_at=pub.last_published_at, published_by=publisher.name if publisher else None)


@router.delete("/{pid}/datasets/{did}/publication", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset_publication(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    dataset = _get_dataset(db, project.id, did)
    await superset_publish.unpublish_dataset(db, dataset.id)


@router.get("/{pid}/publications", response_model=list[DatasetPublicationOut])
def list_dataset_publications(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Bulk read for the canvas' "Publié" badges — one call for every gold node instead of
    one per dataset, same shape as the quality endpoint's qualityByDataset lookup."""
    pubs = db.query(SupersetPublication).filter(SupersetPublication.project_id == project.id).all()
    out = []
    for pub in pubs:
        publisher = db.get(User, pub.published_by)
        out.append(DatasetPublicationOut(dataset_id=pub.medallion_dataset_id, url=pub.published_url, last_published_at=pub.last_published_at, published_by=publisher.name if publisher else None))
    return out


@router.get("/{pid}/dashboards", response_model=list[DashboardSummaryOut])
def list_dashboards(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Bulk read for the canvas' "Dashboard" badges — one call for every gold node, same shape
    as the publications list."""
    specs = db.query(DashboardSpec).filter(DashboardSpec.project_id == project.id).all()
    out = []
    for spec in specs:
        pub = (
            db.query(SupersetPublication)
            .filter(SupersetPublication.medallion_dataset_id == spec.medallion_dataset_id, SupersetPublication.superset_instance_id == spec.superset_instance_id)
            .first()
        )
        if pub is None or pub.superset_dashboard_id is None:
            continue
        instance = db.get(SupersetInstance, spec.superset_instance_id)
        if instance is None:
            continue
        config = get_superset_config(instance)
        out.append(DashboardSummaryOut(
            dataset_id=spec.medallion_dataset_id,
            dashboard_url=f"{config.base_url.rstrip('/')}/superset/dashboard/{pub.superset_dashboard_id}/",
            charts_count=len(spec.indicators),
            last_generated_at=spec.last_generated_at,
        ))
    return out


def _pipeline_agent_context(db: Session, project_id: int, dataset_id: int) -> str | None:
    """A gold dataset built by the Module 13 agent carries its original business objective as
    a durable fact about the dataset, not just a one-time hand-off click — so this is checked
    on every suggest-indicators call (button hand-off, page reload, a "Re-suggérer" days
    later), not only threaded through frontend navigation state, which would silently lose it
    the moment the user leaves and comes back. `execution_state.dataset_ids` maps every plan
    layer's name to its real dataset id; matching against `plan["gold"]` names specifically
    (rather than any name) avoids misattributing a bronze/silver dataset's id to this context
    in the (currently impossible, but not worth relying on) case of a name collision."""
    plan_row = db.query(PipelinePlan).filter(PipelinePlan.project_id == project_id).first()
    if plan_row is None or not plan_row.plan or not plan_row.instruction:
        return None
    dataset_ids = (plan_row.execution_state or {}).get("dataset_ids") or {}
    gold_names = {g["name"] for g in plan_row.plan.get("gold", [])}
    if any(name in gold_names and did_ == dataset_id for name, did_ in dataset_ids.items()):
        return plan_row.instruction
    return None


@router.post("/{pid}/datasets/{did}/suggest-indicators", response_model=SuggestIndicatorsOut)
async def suggest_indicators_endpoint(did: int, context: str | None = None, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Module 12 étape 2 — read-only (Module 9 visibility): profiles the published gold table
    and proposes indicators. Never blocks on Mistral: unreachable/invalid → heuristic fallback.
    `context` (optional query param, Module 13 §6.2): the pipeline agent's original business
    instruction, when this call is reached via the execution stepper's dashboard hand-off. An
    explicit query param always wins; otherwise it's auto-derived from the dataset's own
    provenance (see _pipeline_agent_context) so the AI-generated dashboard prompt stays
    grounded in the objective the user actually asked for, however this endpoint was reached."""
    dataset = _get_dataset(db, project.id, did)
    context = context or _pipeline_agent_context(db, project.id, did)
    pub = db.query(SupersetPublication).filter(SupersetPublication.medallion_dataset_id == dataset.id).first()
    publisher = db.get(User, pub.published_by) if pub else None

    profile_result = gold_profile.profile_gold_dataset(db, project, dataset)
    if profile_result.status != "ok":
        return SuggestIndicatorsOut(
            status=profile_result.status, message=profile_result.message,
            last_published_at=pub.last_published_at if pub else None, published_by=publisher.name if publisher else None,
        )

    ai_client.reset_debug_log()
    config = get_ai_config()
    result = await indicator_suggest.suggest_indicators(config, profile_result.columns, context)
    indicators = [
        SuggestedIndicatorOut(title=i.title, viz_type=i.viz_type, metric_column=i.metric_column, aggregation=i.aggregation, dimension_columns=i.dimension_columns, time_column=i.time_column, slots=i.slots, ranking=i.ranking)
        for i in result.indicators
    ]
    columns = [ProfiledColumnOut(name=c.name, role=c.role) for c in profile_result.columns]
    return SuggestIndicatorsOut(
        status="ok", indicators=indicators, source=result.source, columns=columns,
        last_published_at=pub.last_published_at if pub else None, published_by=publisher.name if publisher else None,
        ai_debug=ai_client.get_debug_log(), warnings=result.warnings,
    )


@router.post("/{pid}/datasets/{did}/generate-dashboard", response_model=GenerateDashboardOut)
async def generate_dashboard_endpoint(
    did: int, payload: GenerateDashboardRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project),
):
    """Module 12 étape 3 — write, owner-only (Module 9): persists the validated contract and
    builds/updates the `dp_` charts + dashboard in one operation."""
    dataset = _get_dataset(db, project.id, did)
    indicators = [
        superset_publish.IndicatorSpec(title=i.title, viz_type=i.viz_type, metric_column=i.metric_column, aggregation=i.aggregation, dimension_columns=i.dimension_columns, time_column=i.time_column, slots=i.slots, ranking=i.ranking)
        for i in payload.indicators
    ]
    outcome = await superset_publish.generate_dashboard(db, project, dataset, indicators, validated_by_id=current_user.id, source=payload.source)
    return GenerateDashboardOut(status=outcome.status, message=outcome.message, dashboard_url=outcome.dashboard_url, charts_count=outcome.charts_count)


@router.post("/{pid}/datasets/{did}/regenerate-dashboard", response_model=GenerateDashboardOut)
async def regenerate_dashboard_endpoint(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """Replays the persisted contract — never calls Mistral."""
    dataset = _get_dataset(db, project.id, did)
    outcome = await superset_publish.regenerate_dashboard(db, project, dataset)
    return GenerateDashboardOut(status=outcome.status, message=outcome.message, dashboard_url=outcome.dashboard_url, charts_count=outcome.charts_count)


@router.get("/{pid}/datasets/{did}/dashboard", response_model=DashboardStatusOut)
def get_dashboard_status_endpoint(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    dataset = _get_dataset(db, project.id, did)
    return DashboardStatusOut(**superset_publish.get_dashboard_status(db, dataset))


@router.delete("/{pid}/datasets/{did}/dashboard", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard_endpoint(did: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    dataset = _get_dataset(db, project.id, did)
    await superset_publish.delete_dashboard_and_charts(db, dataset)


# ---------------- Preview / Build / Run ----------------

@router.post("/{pid}/preview", response_model=PreviewResult)
def preview_project(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    warehouse = db.get(DataSource, project.warehouse_source_id)
    object_store = db.get(DataSource, project.object_store_source_id)
    instance = db.get(AirflowInstance, project.airflow_instance_id)

    warnings = []
    if not any(d.layer == MedallionLayer.bronze for d in datasets):
        warnings.append("Aucun dataset bronze : le pipeline n'ingérera aucune donnée.")
    if not warehouse or not object_store or not instance:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source ou instance Airflow manquante pour ce projet.")

    warehouse_dict = {"host": warehouse.host, "port": warehouse.port, "username": warehouse.username, "password": "••••••••", "database_name": warehouse.database_name}
    try:
        dbt_files = dbt_project.generate_project_files(db, project, datasets, warehouse_dict)
        dag_py = dag_render.render_dag(project, datasets, object_store)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Génération impossible : {exc}")

    dbt_sql = {path: content for path, content in dbt_files.items() if path.endswith(".sql")}
    python_tasks = {
        d.name: d.python_code or ""
        for d in datasets
        if d.layer == MedallionLayer.gold and d.transform_type == TransformType.python
    }
    return PreviewResult(dbt_sql=dbt_sql, python_tasks=python_tasks, dag_py=dag_py, warnings=warnings)


@router.post("/{pid}/validate-sql", response_model=SqlValidationOut)
def validate_dataset_sql(
    payload: SqlValidationRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project),
):
    """UX ask — the dataset editor's "Valider la syntaxe" button: resolves every {{ ref(...) }}
    in the draft SQL against everything actually buildable project-wide (build_ref_map — real
    silver/gold datasets, plus the structuration stages of any bronze with a saved contract),
    then runs the compiled result through Postgres's EXPLAIN — real syntax/column/type
    validation, without saving anything or touching a real dbt build."""
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    bronze_ids = [d.id for d in datasets if d.layer == MedallionLayer.bronze]
    structured_ids = (
        {r[0] for r in db.query(PayloadStructuration.dataset_id).filter(
            PayloadStructuration.dataset_id.in_(bronze_ids), PayloadStructuration.contract_hash.isnot(None),
        ).all()}
        if bronze_ids else set()
    )
    structured_bronze_names = {d.name for d in datasets if d.id in structured_ids}
    ref_map = build_ref_map(datasets, structured_bronze_names)
    custom_macros = db.query(DbtMacro).all()
    extra_macros_src = dbt_macros.macros_source_for_validation(custom_macros)

    try:
        compiled = payload_structure.compile_adhoc_sql(payload.sql, ref_map, extra_macros_src)
    except payload_structure.PayloadStructureError as exc:
        return SqlValidationOut(valid=False, message=str(exc))

    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        return SqlValidationOut(valid=False, message="Warehouse introuvable.")
    try:
        payload_structure.explain_sql(warehouse, compiled)
    except payload_structure.PayloadStructureError as exc:
        return SqlValidationOut(valid=False, message=str(exc), compiled_sql=compiled)
    return SqlValidationOut(valid=True, compiled_sql=compiled)


def _resolve_binding_deploy_target(db: Session, project: MedallionProject, binding: ProjectEnvironmentBinding) -> airflow_instances.DeployTarget:
    instance = db.get(AirflowInstance, binding.airflow_instance_id)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Instance Airflow introuvable pour ce binding.")
    try:
        return airflow_instances.resolve_deploy_target(db, instance, project.dbt_project_name)
    except airflow_instances.AirflowInstanceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _build_binding(db: Session, project: MedallionProject, binding: ProjectEnvironmentBinding, current_user: User, confirm_impact: bool = False) -> BuildReport:
    """Module 17 §4.1 — the generalized build, shared by the home-alias route and the
    explicit-environment one. Everything infra-shaped (warehouse/object store/Airflow
    instance/schedule) is read from `binding`, never from `project` directly — the only way
    this correctly builds a NON-home binding too, once one exists (Étape 3's promotion)."""
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    if not datasets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ajoutez au moins un dataset avant de déployer.")
    # Module 19 §4.2 — a workspace left in "parse en erreur" by the last code edit blocks
    # every build (any binding), until a fix through the Code tab clears it.
    if project.workspace_parse_status == WorkspaceParseStatus.error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le code du projet contient des erreurs (onglet Code) — corrigez-les avant de déployer.")
    # Module 19 §5.6 — a proposal/conflict left over from a previous build blocks every
    # later one too, without even attempting to regenerate.
    pending = workspace_merge.list_active_conflicts(db, project.id)
    if pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{len(pending)} fichier(s) à arbitrer (onglet Code) avant de déployer.")
    # Module 19 §7.3 — "Revue des changements de code" : non bloquant (ce module n'invente
    # aucun gate — seul un check M16 en « Bloquer » bloque quoi que ce soit), mais exige une
    # confirmation explicite la première fois. Un projet jamais construit, ou dont le code n'a
    # pas divergé de ce qui est déjà en ligne, n'a rien à revoir.
    impact_result = impact.analyze(db, project)
    if impact_result.has_impact and not confirm_impact:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "message": "Ce déploiement modifie des colonnes utilisées en aval — confirmez pour continuer.",
            "impact": [m.as_dict() for m in impact_result.models],
        })

    warehouse = db.get(DataSource, binding.warehouse_source_id)
    object_store = db.get(DataSource, binding.object_store_source_id)
    target = _resolve_binding_deploy_target(db, project, binding)

    try:
        report = await build_project(db, project, binding, datasets, warehouse, object_store, target)
    except workspace_merge.WorkspaceConflictsPending as exc:
        # This regeneration itself just created (or left standing) an active conflict — the
        # proposal is already persisted (see medallion_deploy's own commit-on-exception
        # path), ready for the "À arbitrer" view; no deployment happened.
        binding.status = ProjectStatus.error
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except workspace_sync.WorkspaceParseFailed as exc:
        # §7.3's "compilation OK" precondition, re-checked right after this regeneration's own
        # merge — the file(s) involved are already saved (workspace_sync never rolls that
        # back), only the deploy itself didn't happen.
        binding.status = ProjectStatus.error
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        binding.status = ProjectStatus.error
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    binding.dag_id = report["dag_id"]
    binding.dag_file_path = report["dag_path"]
    binding.status = ProjectStatus.deployed
    if binding.is_home:
        # has_pending_changes tracks staleness against the HOME binding's own last deploy
        # specifically (§3.3) — building a non-home binding never touches it.
        project.has_pending_changes = False
    db.commit()

    try:
        version_snapshot.capture(
            db, project, binding, datasets, report["dbt_files_content"], report["dag_content"],
            report["dag_id_base"], report["dag_path"], current_user,
        )
    except Exception:
        logger.exception("version snapshot capture failed for project %s (binding %s)", project.id, binding.id)

    return BuildReport(
        dbt_files=report["dbt_files"], connections_created=report["connections_created"], dag_deposited=True,
        activated=report.get("activated"), activation_error=report.get("activation_error"),
    )


@router.post("/{pid}/build", response_model=BuildReport)
async def build_project_endpoint(payload: BuildRequest | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project)):
    """Kept as a thin alias onto the home binding (§4.3 compat) — every existing caller of
    this route keeps working exactly as before, unaware bindings exist at all. `payload` is
    optional (Module 19 §7.3's `confirm_impact`) precisely so every pre-existing no-body
    caller keeps working unchanged."""
    return await _build_binding(db, project, project.home_binding, current_user, (payload or BuildRequest()).confirm_impact)


@router.post("/{pid}/bindings/{env}/build", response_model=BuildReport)
async def build_binding_endpoint(env: str, payload: BuildRequest | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project)):
    binding = get_project_binding(project.id, env, db, project)
    return await _build_binding(db, project, binding, current_user, (payload or BuildRequest()).confirm_impact)


@router.get("/{pid}/bindings", response_model=list[BindingOut])
def list_bindings(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    return db.query(ProjectEnvironmentBinding).filter(ProjectEnvironmentBinding.project_id == project.id).order_by(ProjectEnvironmentBinding.is_home.desc()).all()


# ---------------- Promotion (Module 17 §5) ----------------

@router.post("/{pid}/bindings/prod", response_model=BindingOut)
def upsert_prod_binding_endpoint(payload: ProdBindingUpsert, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """§5.3 — creates the prod binding on first call (status=draft), updates it on any
    later call. Also (re)syncs its source-mapping rows against the project's current bronze
    datasets — a newly-added bronze dataset gets a fresh (suggested, unconfirmed) row without
    disturbing already-confirmed ones."""
    for source_id in (payload.warehouse_source_id, payload.object_store_source_id):
        if db.get(DataSource, source_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source introuvable.")
    if db.get(AirflowInstance, payload.airflow_instance_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Instance Airflow introuvable.")
    if payload.schedule:
        try:
            schedule.validate_schedule(payload.schedule)
        except schedule.ScheduleError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return promotion.upsert_prod_binding(
        db, project, payload.airflow_instance_id, payload.warehouse_source_id,
        payload.object_store_source_id, payload.schedule, payload.target,
    )


@router.get("/{pid}/bindings/prod/source-mappings", response_model=list[SourceMappingOut])
def list_prod_source_mappings(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    prod_binding = promotion.get_prod_binding(db, project.id)
    if prod_binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun binding prod configuré.")
    return promotion.list_source_mappings(db, prod_binding)


@router.put("/{pid}/bindings/prod/source-mappings/{origin_source_id}", response_model=SourceMappingOut)
def confirm_prod_source_mapping(origin_source_id: int, payload: SourceMappingConfirm, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    prod_binding = promotion.get_prod_binding(db, project.id)
    if prod_binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun binding prod configuré.")
    try:
        promotion.confirm_source_mapping(db, prod_binding, origin_source_id, payload.target_source_id)
    except promotion.PromotionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    origin = next((m for m in promotion.list_source_mappings(db, prod_binding) if m.origin_source_id == origin_source_id), None)
    return origin


@router.get("/{pid}/promote/preview", response_model=PromotionPreviewOut)
def get_promotion_preview(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    return promotion.promotion_preview(db, project)


@router.post("/{pid}/promote", response_model=BindingOut)
async def promote_endpoint(payload: PromoteRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    """§5.4/§5.5 — owner-only (promoting is writing, §0). Refuses without explicit
    confirmation, an undeployed dev, an unmapped source, or an open critical quality alert;
    never triggers a run (the engineer launches prod when ready, same M8 spirit as restore)."""
    if not payload.confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Confirmation requise pour promouvoir en prod.")
    try:
        return await promotion.promote(db, project)
    except promotion.PromotionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/{pid}/deploy-status", response_model=DeployStatusOut)
async def get_deploy_status(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    """Lets the UI show real deployment progress instead of guessing: a file deposited via
    SFTP is not yet a DAG Airflow knows about — the dag-processor only picks it up on its
    next scan (up to dag_dir_list_interval, 300s by default). Polled by the frontend after
    build, reusing the same get_dag() lookup the preflight reparse check already relies on.

    Module 3 correctif — this is also the *reliable* activation point for a scheduled
    project: build_project()'s own unpause attempt almost always fires before Airflow has
    parsed the just-deposited file (immediate reparse via the API isn't dependable — see
    api/routes/airflow.py) and so typically no-ops. The first poll where the DAG is
    genuinely known is the first moment an unpause call can actually succeed — firing it
    here means that, from the caller's perspective, the DAG "appears" already active."""
    if not project.dag_id:
        return DeployStatusOut(known_to_airflow=False)
    airflow_conf = _resolve_airflow_config(db, project)
    try:
        dag = await airflow_api.get_dag(airflow_conf.base_url, airflow_conf.username, airflow_conf.password, project.dag_id)
    except airflow_api.AirflowAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    activated = None
    if dag is not None and project.schedule:
        if dag.get("is_paused") is False:
            activated = True  # already active — a prior attempt (or a manual one) succeeded
        else:
            try:
                await airflow_api.set_dag_paused(airflow_conf.base_url, airflow_conf.username, airflow_conf.password, project.dag_id, False)
                activated = True
            except airflow_api.AirflowAPIError:
                activated = False

    return DeployStatusOut(known_to_airflow=dag is not None, activated=activated)


# ---------------- Versions (Module 8) ----------------

@router.get("/{pid}/versions", response_model=list[VersionOut])
def list_versions(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    versions = db.query(MedallionVersion).filter(MedallionVersion.project_id == project.id).order_by(MedallionVersion.version_number.desc()).all()
    user_ids = {v.created_by_id for v in versions if v.created_by_id}
    names = dict(db.query(User.id, User.name).filter(User.id.in_(user_ids)).all()) if user_ids else {}
    numbers_by_id = {v.id: v.version_number for v in versions}
    result = []
    for v in versions:
        out = VersionOut.model_validate(v)
        out.created_by_name = names.get(v.created_by_id)
        out.is_restore_of_version_number = numbers_by_id.get(v.is_restore_of)
        result.append(out)
    return result


@router.get("/{pid}/versions/{vid}", response_model=VersionDetailOut)
def get_version(vid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    version = db.query(MedallionVersion).filter(MedallionVersion.id == vid, MedallionVersion.project_id == project.id).first()
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version introuvable.")
    out = VersionDetailOut.model_validate(version)
    if version.created_by_id:
        user = db.get(User, version.created_by_id)
        out.created_by_name = user.name if user else None
    if version.is_restore_of:
        restored = db.get(MedallionVersion, version.is_restore_of)
        out.is_restore_of_version_number = restored.version_number if restored else None
    return out


def _get_version_or_404(db: Session, pid: int, vid: int) -> MedallionVersion:
    version = db.query(MedallionVersion).filter(MedallionVersion.id == vid, MedallionVersion.project_id == pid).first()
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version introuvable.")
    return version


@router.get("/{pid}/versions/{vid}/diff", response_model=VersionDiffOut)
def get_version_diff(vid: int, against: str = "active", db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    base = _get_version_or_404(db, project.id, vid)

    if against == "active":
        if project.active_version_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce projet n'a pas de version active.")
        target = db.get(MedallionVersion, project.active_version_id)
    else:
        try:
            against_id = int(against)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Paramètre 'against' invalide.")
        target = _get_version_or_404(db, project.id, against_id)

    result = version_diff.diff(base, target)
    return VersionDiffOut(base_version_number=base.version_number, target_version_number=target.version_number, **result)


@router.post("/{pid}/versions/{vid}/restore", response_model=VersionOut)
async def restore_version(vid: int, payload: RestoreRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), project: MedallionProject = Depends(get_owned_project)):
    if not payload.confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Confirmation requise pour restaurer une version.")
    if project.active_version_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce projet n'a pas encore d'historique de déploiement.")

    target_version = _get_version_or_404(db, project.id, vid)
    warehouse = db.get(DataSource, project.warehouse_source_id)
    object_store = db.get(DataSource, project.object_store_source_id)
    deploy_target = _resolve_deploy_target(db, project)

    try:
        new_version = await version_restore.restore(db, project, target_version, warehouse, object_store, deploy_target, current_user)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Restauration impossible : {exc}")

    out = VersionOut.model_validate(new_version)
    out.created_by_name = current_user.name
    out.is_restore_of_version_number = target_version.version_number
    return out


@router.post("/{pid}/run", response_model=list[RunOut], status_code=status.HTTP_201_CREATED)
async def run_project(payload: RunTriggerRequest, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    if not project.dag_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Déployez le projet avant de l'exécuter.")

    airflow_conf = _resolve_airflow_config(db, project)

    # A DAG with no schedule stays paused after deploy by design (Module 3 correctif — a
    # manual project rests paused between runs). But Airflow's scheduler never executes ANY
    # run, including this manually-triggered one, while its DAG is paused — it would just sit
    # in "queued" forever with no visible error. "Exécuter maintenant" is exactly the case
    # where that pause must be lifted for the trigger to actually do anything.
    try:
        dag = await airflow_api.get_dag(airflow_conf.base_url, airflow_conf.username, airflow_conf.password, project.dag_id)
        if dag is not None and dag.get("is_paused"):
            await airflow_api.set_dag_paused(airflow_conf.base_url, airflow_conf.username, airflow_conf.password, project.dag_id, False)
    except airflow_api.AirflowAPIError:
        pass  # best-effort — if this fails, trigger_dag_run below will surface its own clear error

    dates = [None]
    if payload.backfill_from and payload.backfill_to:
        if payload.backfill_to < payload.backfill_from:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La date de fin doit suivre la date de début.")
        span = (payload.backfill_to - payload.backfill_from).days
        dates = [payload.backfill_from + timedelta(days=i) for i in range(span + 1)]

    runs = []
    for d in dates:
        logical_date = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) if d else None
        try:
            result = await airflow_api.trigger_dag_run(airflow_conf.base_url, airflow_conf.username, airflow_conf.password, project.dag_id, logical_date)
        except airflow_api.AirflowAPIError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

        run = MedallionRun(project_id=project.id, dag_run_id=result.get("dag_run_id"), state=RunState.queued, started_at=datetime.now(timezone.utc))
        db.add(run)
        runs.append(run)

    db.commit()
    for r in runs:
        db.refresh(r)
    return runs


@router.post("/{pid}/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause_project(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    await _set_paused(project, True, db)
    project.status = ProjectStatus.paused
    db.commit()


@router.post("/{pid}/unpause", status_code=status.HTTP_204_NO_CONTENT)
async def unpause_project(db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    await _set_paused(project, False, db)
    project.status = ProjectStatus.deployed
    db.commit()


async def _set_paused(project: MedallionProject, is_paused: bool, db: Session):
    if not project.dag_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce projet n'est pas encore déployé.")
    airflow_conf = _resolve_airflow_config(db, project)
    try:
        await airflow_api.set_dag_paused(airflow_conf.base_url, airflow_conf.username, airflow_conf.password, project.dag_id, is_paused)
    except airflow_api.AirflowAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


# ---------------- Lineage & Runs ----------------

def build_lineage_graph(db: Session, datasets: list[MedallionDataset]) -> LineageGraph:
    """Shared by the owner-facing route and the admin read-only detail view (Module 9) —
    the origin-node derivation is non-trivial enough that duplicating it would be a real
    maintenance risk."""
    # Module 6 extension (payload & structuration) — cheap, control-plane-only, batched once
    # here rather than per-node; drives the canvas's synthetic structuration node (§ the
    # extension's own canvas-UX addition, not in the original spec text).
    payload_backed = payload_structure.bulk_payload_backed(db, datasets)
    structured = payload_structure.bulk_structured(db, datasets)
    code_modified = workspace.bulk_code_modified(db, [d.id for d in datasets])

    nodes = [
        LineageNode(
            id=d.id, name=d.name, node_type="dataset", layer=d.layer, transform_type=d.transform_type, ml_objective=d.ml_objective,
            last_row_count=d.last_row_count, last_loaded_at=d.last_loaded_at, last_test_status=d.last_test_status,
            payload_backed=payload_backed.get(d.id, False), structured=structured.get(d.id, False),
            code_modified=code_modified.get(d.id, False),
        )
        for d in datasets
    ]
    edges = [LineageEdge(source=u, target=d.id) for d in datasets for u in d.upstream_dataset_ids]

    # Every bronze dataset gets a derived "origin" node — never stored, just computed from
    # source_id + source_object, which already exist. A dbt/MinIO/Oracle bronze looks the
    # same as a file-import one here; only the join below tells them apart.
    bronze = [d for d in datasets if d.layer == MedallionLayer.bronze and d.source_id]
    source_ids = {d.source_id for d in bronze}
    sources_by_id = {s.id: s for s in db.query(DataSource).filter(DataSource.id.in_(source_ids)).all()} if source_ids else {}

    imports_by_key: dict[tuple[int, str, str], FileImport] = {}
    if source_ids:
        completed = (
            db.query(FileImport)
            .filter(FileImport.target_source_id.in_(source_ids), FileImport.status == FileImportStatus.imported)
            .all()
        )
        imports_by_key = {(fi.target_source_id, fi.target_schema, fi.target_table): fi for fi in completed}

    # Module 6 extension §4.5/§5.5 — a watched origin gets a "surveillé" badge + its last real
    # trigger, purely informational: the origin node stays a derived, never-executed-by-the-DAG
    # node either way (the invariant this whole extension is built not to touch).
    watches_by_import_id: dict[int, FileWatch] = {}
    if imports_by_key:
        watches = db.query(FileWatch).filter(FileWatch.file_import_id.in_([fi.id for fi in imports_by_key.values()])).all()
        watches_by_import_id = {w.file_import_id: w for w in watches}

    origin_ids: dict[tuple[int, str], int] = {}
    next_origin_id = -1
    for d in bronze:
        key = (d.source_id, d.source_object)
        if key not in origin_ids:
            origin_ids[key] = next_origin_id
            next_origin_id -= 1

            source = sources_by_id.get(d.source_id)
            provenance = None
            if source and source.type == DataSourceType.postgresql and d.source_object and "." in d.source_object:
                schema_name, table_name = d.source_object.split(".", 1)
                fi = imports_by_key.get((d.source_id, schema_name, table_name))
                if fi is not None:
                    watch = watches_by_import_id.get(fi.id)
                    provenance = {
                        "type": "file_import", "import_id": fi.id, "file": fi.source_file_name,
                        "imported_at": fi.imported_at.isoformat() if fi.imported_at else None, "row_count": fi.row_count,
                        "watched": watch is not None,
                        "last_triggered_at": watch.last_triggered_at.isoformat() if watch and watch.last_triggered_at else None,
                    }

            label = d.source_object or (source.name if source else "?")
            nodes.append(LineageNode(
                id=origin_ids[key], name=label, node_type="origin",
                source_type=source.type.value if source else None,
                provenance=provenance,
            ))
        edges.append(LineageEdge(source=origin_ids[key], target=d.id))

    return LineageGraph(nodes=nodes, edges=edges)


@router.get("/{pid}/lineage", response_model=LineageGraph)
def get_lineage(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    return build_lineage_graph(db, datasets)


@router.get("/{pid}/runs", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    return db.query(MedallionRun).filter(MedallionRun.project_id == project.id).order_by(MedallionRun.created_at.desc()).all()


@router.get("/{pid}/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    run = db.query(MedallionRun).filter(MedallionRun.id == run_id, MedallionRun.project_id == project.id).first()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run introuvable.")
    target = _resolve_deploy_target(db, project)
    return await run_status.refresh_run_state(db, project, run, target)
