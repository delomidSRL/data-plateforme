from datetime import datetime, date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.medallion import (
    LoadMode,
    Materialization,
    MedallionLayer,
    MLObjective,
    ProjectStatus,
    ProjectTarget,
    RunState,
    TestStatus,
    TransformType,
)


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def _trim(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Le nom du dossier ne peut pas être vide.")
        return self


class FolderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def _trim(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Le nom du dossier ne peut pas être vide.")
        return self


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    project_count: int = 0
    created_at: datetime


class ProjectFolderUpdate(BaseModel):
    folder_id: int | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    object_store_source_id: int
    warehouse_source_id: int
    airflow_instance_id: int
    superset_instance_id: int | None = None
    dbt_project_name: str = Field(min_length=1, max_length=120)
    target: ProjectTarget = ProjectTarget.dev
    schedule: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    schedule: str | None = None
    target: ProjectTarget | None = None
    superset_instance_id: int | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    object_store_source_id: int
    warehouse_source_id: int
    airflow_instance_id: int
    superset_instance_id: int | None = None
    dbt_project_name: str
    target: ProjectTarget
    schedule: str | None = None
    dag_id: str | None = None
    dag_file_path: str | None = None
    status: ProjectStatus
    has_pending_changes: bool
    active_version_id: int | None = None
    created_at: datetime
    # Module 15 — presentation-only grouping, never touches build/run/lineage/RBAC.
    folder_id: int | None = None
    # Module 17 — the home binding's environment (dev|prod); read-only, drives the project
    # header's environment badge (§3.6).
    environment: str
    # Module 19 §8 — set by workspace_sync after a code edit; "error" blocks every build.
    workspace_parse_status: str = "ok"
    workspace_parse_errors: list[dict] | None = None


class DatasetCreate(BaseModel):
    layer: MedallionLayer
    name: str = Field(min_length=1, max_length=120)

    # bronze
    source_id: int | None = None
    source_object: str | None = None
    load_mode: LoadMode | None = None
    incremental_key: str | None = None
    partition_by: str | None = None

    # silver/gold (dbt)
    dbt_model_name: str | None = None
    materialization: Materialization | None = None
    sql: str | None = None

    # gold (Python / Calcul ML)
    transform_type: TransformType = TransformType.dbt
    ml_objective: MLObjective = MLObjective.none
    python_code: str | None = None
    template_id: int | None = None
    output_table: str | None = None

    upstream_dataset_ids: list[int] = Field(default_factory=list)
    tests: list[dict] = Field(default_factory=list)
    description: str | None = None

    @model_validator(mode="after")
    def _validate_layer_fields(self):
        if self.layer == MedallionLayer.bronze:
            if not self.source_id or not self.source_object:
                raise ValueError("Un dataset bronze nécessite source_id et source_object.")
            self.load_mode = self.load_mode or LoadMode.full
        elif self.transform_type == TransformType.python:
            if self.layer != MedallionLayer.gold:
                raise ValueError("Un nœud Python (Calcul ML) doit être de couche gold.")
            if not self.python_code:
                raise ValueError("Un nœud Python nécessite du code (python_code).")
        else:
            if not self.dbt_model_name or not self.sql:
                raise ValueError("Un dataset silver/gold nécessite dbt_model_name et sql.")
            self.materialization = self.materialization or Materialization.view
        return self


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_id: int | None = None
    source_object: str | None = None
    load_mode: LoadMode | None = None
    incremental_key: str | None = None
    partition_by: str | None = None
    dbt_model_name: str | None = None
    materialization: Materialization | None = None
    sql: str | None = None
    transform_type: TransformType | None = None
    ml_objective: MLObjective | None = None
    python_code: str | None = None
    template_id: int | None = None
    output_table: str | None = None
    upstream_dataset_ids: list[int] | None = None
    tests: list[dict] | None = None
    description: str | None = None


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    layer: MedallionLayer
    name: str
    source_id: int | None = None
    source_object: str | None = None
    load_mode: LoadMode | None = None
    incremental_key: str | None = None
    partition_by: str | None = None
    bronze_location: str | None = None
    dbt_model_name: str | None = None
    materialization: Materialization | None = None
    sql: str | None = None
    transform_type: TransformType
    ml_objective: MLObjective
    python_code: str | None = None
    template_id: int | None = None
    output_table: str | None = None
    upstream_dataset_ids: list[int]
    tests: list[dict]
    description: str | None = None
    last_row_count: int | None = None
    last_loaded_at: datetime | None = None
    last_test_status: TestStatus
    created_at: datetime
    # Module 6 extension (payload & structuration) — bronze-only; true when this dataset's
    # source_object resolves to a payload-mode FileImport. Computed, never stored.
    payload_backed: bool = False
    # Module 19 §8 — `visual` (built through the canvas forms) | `code` (its model file was
    # created directly in the Code tab). Stored, unlike the two below.
    origin: str = "visual"
    # §4.4 — computed, never stored: true once this dataset's linked file diverges from its
    # generated base. Drives the canvas badge and the read-only declarative form.
    code_modified: bool = False


class DatasetColumnOut(BaseModel):
    column: str
    type: str


class DatasetColumnsOut(BaseModel):
    columns: list[DatasetColumnOut]
    table_exists: bool
    # true when `columns` came from a saved structuration contract (the dataset's
    # `05_validated_<name>` model, Module 18 §7.5) rather than the raw bronze payload table —
    # callers authoring SQL should then reference `{{ ref('05_validated_<name>') }}`, not
    # `{{ source('bronze', ...) }}`.
    structured: bool = False


class PreviewResult(BaseModel):
    dbt_sql: dict[str, str]
    python_tasks: dict[str, str] = {}
    dag_py: str
    warnings: list[str] = []


class DataSampleColumnOut(BaseModel):
    name: str
    type: str


class DataSampleTargetOut(BaseModel):
    kind: str  # "materialized" | "source"
    schema_name: str
    table: str
    source_name: str


class DataSampleOut(BaseModel):
    status: str  # ok | not_materialized | not_found | unreachable | timeout
    message: str | None = None
    columns: list[DataSampleColumnOut] = []
    rows: list[dict] = []
    truncated: bool = False
    target: DataSampleTargetOut | None = None


# UX ask — the dataset editor's "Valider la syntaxe" button: ad-hoc dbt SQL, checked without
# saving anything or touching a real dbt build (payload_structure.compile_adhoc_sql +
# explain_sql).
class SqlValidationRequest(BaseModel):
    sql: str


class SqlValidationOut(BaseModel):
    valid: bool
    message: str | None = None
    compiled_sql: str | None = None


class PublishRequest(BaseModel):
    superset_instance_id: int | None = None


class PublishResultOut(BaseModel):
    status: str  # ok | not_materialized | not_gold | unreachable | bad_credentials | superset_error
    message: str | None = None
    url: str | None = None
    superset_dataset_id: int | None = None
    columns_count: int | None = None


class PublicationStatusOut(BaseModel):
    published: bool
    url: str | None = None
    last_published_at: datetime | None = None
    published_by: str | None = None


class DatasetPublicationOut(BaseModel):
    dataset_id: int
    published: bool = True
    url: str | None = None


class ExportLogOut(BaseModel):
    """Module 11 extension — one line of the export journal, enriched for the recent-exports
    view (dataset + user resolved to names)."""
    id: int
    dataset_id: int | None = None
    dataset_name: str | None = None
    kind: str
    exported_at: datetime
    exported_by: str | None = None
    row_count: int | None = None
    client_ip: str | None = None


class SuggestedIndicatorOut(BaseModel):
    title: str
    viz_type: str
    metric_column: str
    aggregation: str
    dimension_columns: list[str] = []
    time_column: str | None = None
    slots: dict[str, dict] | None = None
    ranking: dict | None = None


class ProfiledColumnOut(BaseModel):
    name: str
    role: str  # measure | dimension | temporal


class SuggestIndicatorsOut(BaseModel):
    status: str  # ok | not_gold | not_materialized | unreachable
    message: str | None = None
    indicators: list[SuggestedIndicatorOut] = []
    source: str | None = None  # ai | heuristic — only set when status == ok
    columns: list[ProfiledColumnOut] = []  # so the frontend can populate "change column" menus
    last_published_at: datetime | None = None
    published_by: str | None = None
    ai_debug: list[dict] | None = None
    # Raffinage §4.3 — visible reasons for AI-proposed indicators the deterministic role/
    # precondition guard dropped (e.g. a temporal viz_type on a gold without a temporal
    # column). Mirrors PlanOut.gold_warnings.
    warnings: list[str] = []


class IndicatorIn(BaseModel):
    title: str
    viz_type: str
    metric_column: str
    aggregation: str
    dimension_columns: list[str] = []
    time_column: str | None = None
    slots: dict[str, dict] | None = None
    ranking: dict | None = None


class GenerateDashboardRequest(BaseModel):
    indicators: list[IndicatorIn]
    source: str  # ai | heuristic | manual


class GenerateDashboardOut(BaseModel):
    status: str  # ok | not_published | unreachable | bad_credentials | superset_error
    message: str | None = None
    dashboard_url: str | None = None
    charts_count: int | None = None


class DashboardStatusOut(BaseModel):
    generated: bool
    dashboard_url: str | None = None
    charts_count: int | None = None
    last_generated_at: datetime | None = None


class DashboardSummaryOut(BaseModel):
    dataset_id: int
    dashboard_url: str
    charts_count: int
    last_generated_at: datetime | None = None


class DeployStatusOut(BaseModel):
    known_to_airflow: bool
    # Module 3 correctif — None: not applicable (manual project, or DAG not known yet).
    # True/False: a scheduled project's DAG was just found paused and an unpause was
    # attempted, this being the outcome. Already-unpaused (idempotent re-check) also reports
    # True without a redundant API call.
    activated: bool | None = None


class BuildReport(BaseModel):
    dbt_files: list[str]
    connections_created: list[str]
    dag_deposited: bool
    # Module 3 correctif — None when schedule is null (manual project, nothing to activate);
    # True/False once a scheduled project's immediate activation attempt has run.
    activated: bool | None = None
    activation_error: str | None = None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    version_number: int
    created_by_id: int | None = None
    created_by_name: str | None = None
    created_at: datetime
    dag_id: str | None = None
    dag_file_path: str | None = None
    is_restore_of: int | None = None
    is_restore_of_version_number: int | None = None


class VersionDetailOut(VersionOut):
    dbt_project_snapshot: dict[str, str]
    dag_snapshot: str
    datasets_snapshot: list[dict]


class DatasetSqlDiff(BaseModel):
    name: str
    before: str | None = None
    after: str | None = None


class DatasetTestsDiff(BaseModel):
    name: str
    before: list = []
    after: list = []


class VersionDiffOut(BaseModel):
    base_version_number: int
    target_version_number: int
    datasets_added: list[str]
    datasets_removed: list[str]
    sql_changed: list[DatasetSqlDiff]
    tests_changed: list[DatasetTestsDiff]


class RestoreRequest(BaseModel):
    confirm: bool = False


class RunTriggerRequest(BaseModel):
    backfill_from: date | None = None
    backfill_to: date | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    dag_run_id: str | None = None
    state: RunState
    started_at: datetime | None = None
    finished_at: datetime | None = None
    layer_stats: dict
    tests_summary: dict
    logs_url: str | None = None
    created_at: datetime


class LineageNode(BaseModel):
    id: int
    name: str
    node_type: str = "dataset"  # "dataset" | "origin" — origin nodes are derived, never stored
    layer: MedallionLayer | None = None
    transform_type: TransformType | None = None
    ml_objective: MLObjective | None = None
    last_row_count: int | None = None
    last_loaded_at: datetime | None = None
    last_test_status: TestStatus | None = None
    # origin-only
    source_type: str | None = None
    provenance: dict | None = None
    # Module 6 extension (payload & structuration) — bronze dataset nodes only; drives the
    # canvas's synthetic "structuration" node between this bronze and any silver reading it.
    payload_backed: bool = False
    # Module 18 §7 UX — true once this bronze has an actually-saved contract (not merely
    # payload-backed): the canvas only shows the 01..05 chain / instant unpacked+typed preview
    # once this is true, never just because the bronze happens to be payload-backed.
    structured: bool = False
    # Module 19 §4.4 — drives the canvas node's "Modifié en code" badge.
    code_modified: bool = False


class LineageEdge(BaseModel):
    source: int
    target: int


class LineageGraph(BaseModel):
    nodes: list[LineageNode]
    edges: list[LineageEdge]


# ---------------- Module 19 étape 1 — workspace (read-only explorer) ----------------

class WorkspaceFileOut(BaseModel):
    path: str
    # "generated" (content == base, the normal case in étape 1) | "modified" (diverges from
    # base — only possible from étape 2 on) | "code" (no base at all, human-created file —
    # only possible from étape 2 on).
    status: str
    dataset_id: int | None
    version: int
    generator: str | None
    updated_at: datetime


class WorkspaceTreeOut(BaseModel):
    files: list[WorkspaceFileOut]


class WorkspaceFileContentOut(BaseModel):
    path: str
    content: str
    status: str
    dataset_id: int | None
    version: int
    generator: str | None
    updated_at: datetime


class WorkspaceFileDiffOut(BaseModel):
    path: str
    against: str
    diff: str


# ---------------- Module 19 étape 2 — édition & synchronisation code -> canvas ----------------

class WorkspaceFileWrite(BaseModel):
    path: str
    content: str
    if_version: int


class WorkspaceFileCreate(BaseModel):
    path: str
    content: str = ""


class WorkspaceFileMove(BaseModel):
    from_path: str
    to_path: str
    if_version: int


class WorkspaceFileDelete(BaseModel):
    path: str
    if_version: int
    confirm: bool = False


class SyncErrorOut(BaseModel):
    path: str | None = None
    line: int | None = None
    column: int | None = None
    message: str


class WorkspaceSyncOut(BaseModel):
    ok: bool
    errors: list[SyncErrorOut] = Field(default_factory=list)


class WorkspaceWriteOut(BaseModel):
    path: str
    content: str
    status: str
    dataset_id: int | None
    version: int
    generator: str | None
    updated_at: datetime
    sync: WorkspaceSyncOut
