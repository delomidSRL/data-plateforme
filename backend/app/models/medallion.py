import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class ProjectTarget(str, enum.Enum):
    dev = "dev"
    prod = "prod"


class ProjectStatus(str, enum.Enum):
    draft = "draft"
    built = "built"
    deployed = "deployed"
    paused = "paused"
    error = "error"


class MedallionLayer(str, enum.Enum):
    bronze = "bronze"
    silver = "silver"
    gold = "gold"


class LoadMode(str, enum.Enum):
    full = "full"
    incremental = "incremental"
    append = "append"


class Materialization(str, enum.Enum):
    view = "view"
    table = "table"
    incremental = "incremental"


class TestStatus(str, enum.Enum):
    none = "none"
    passed = "passed"
    failed = "failed"


class TransformType(str, enum.Enum):
    dbt = "dbt"
    python = "python"


class MLObjective(str, enum.Enum):
    none = "none"
    anomaly = "anomaly"
    scoring = "scoring"
    forecast = "forecast"
    clustering = "clustering"
    record_linkage = "record_linkage"


class RunState(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class MedallionFolder(Base):
    """Module 15 — a purely organizational container over a user's own medallion projects
    (Module 9 ownership remains the only authority on visibility/rights; a folder never
    widens or narrows it). Personal: owned by its creator, never shared, never visible to
    another user — including admins, who manage their own folders exactly like an engineer."""
    __tablename__ = "medallion_folders"
    # DB-enforced functional unique index on (owner_id, lower(name)) — the true single source
    # of truth for the "no two same-named folders per owner, case-insensitive" rule, race-safe
    # under concurrent writes (unlike checking-then-inserting in the service layer alone).
    __table_args__ = (Index("uq_medallion_folders_owner_name_lower", "owner_id", text("lower(name)"), unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MedallionProject(Base):
    __tablename__ = "medallion_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Module 9: ownership governs writes, not role — even an admin only writes to their
    # own projects. Set from current_user at creation, never accepted from the client.
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # Module 15 — purely organizational (governs the "Médaillons" list view only, never
    # build/run/lineage/RBAC). null = root ("Sans dossier"). Always the same owner as the
    # project itself — enforced in the service layer (deps_medallion.get_owned_folder), not
    # just by this FK, since the FK alone can't compare owner_id across the two tables.
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("medallion_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    # Module 11: optional — a project can be created without picking a Superset instance and
    # have one attached later, right when it's first published (see publish_dataset_endpoint).
    # Not environment-specific (Module 17 doesn't move it — out of that module's scope).
    superset_instance_id: Mapped[int | None] = mapped_column(ForeignKey("superset_instances.id", ondelete="SET NULL"), nullable=True)
    dbt_project_name: Mapped[str] = mapped_column(String(120), nullable=False)
    has_pending_changes: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Module 17 — object_store_source_id, warehouse_source_id, airflow_instance_id, target,
    # schedule, dag_id, dag_file_path, status and active_version_id used to be columns here;
    # they now live on ProjectEnvironmentBinding, one row per (project, environment) — a
    # project's *logic* is environment-agnostic, only its *liaison* to infra varies. String
    # target (not a direct class import) avoids a medallion.py <-> project_environment_binding
    # circular import; SQLAlchemy resolves it from the shared declarative registry.
    bindings: Mapped[list["ProjectEnvironmentBinding"]] = relationship(
        "ProjectEnvironmentBinding", backref="project", cascade="all, delete-orphan", lazy="selectin",
    )

    @property
    def home_binding(self) -> "ProjectEnvironmentBinding":
        """The binding created together with the project (its dev/working environment) —
        every project has exactly one, from creation onward (enforced in the service layer
        that creates projects/bindings, not a DB constraint — see §"is_home" on the model)."""
        for b in self.bindings:
            if b.is_home:
                return b
        raise RuntimeError(f"MedallionProject {self.id} has no home binding — data integrity bug.")

    # --- read/write shims over the home binding (Module 17 §3.5: "toutes les lectures qui
    # liaient un projet à sa stack/warehouse/source passent désormais par le binding home par
    # défaut, comportement identique à aujourd'hui") — every one of the many existing call
    # sites that used to read/write `project.warehouse_source_id` etc. directly keeps working
    # completely unchanged; only code that must operate on a specific *non-home* binding
    # (Module 17's own build-by-binding / promotion machinery) bypasses these and addresses a
    # ProjectEnvironmentBinding directly. ---
    @property
    def object_store_source_id(self) -> int:
        return self.home_binding.object_store_source_id

    @object_store_source_id.setter
    def object_store_source_id(self, value: int) -> None:
        self.home_binding.object_store_source_id = value

    @property
    def warehouse_source_id(self) -> int:
        return self.home_binding.warehouse_source_id

    @warehouse_source_id.setter
    def warehouse_source_id(self, value: int) -> None:
        self.home_binding.warehouse_source_id = value

    @property
    def airflow_instance_id(self) -> int:
        return self.home_binding.airflow_instance_id

    @airflow_instance_id.setter
    def airflow_instance_id(self, value: int) -> None:
        self.home_binding.airflow_instance_id = value

    @property
    def target(self) -> "ProjectTarget":
        return self.home_binding.target

    @target.setter
    def target(self, value: "ProjectTarget") -> None:
        self.home_binding.target = value

    @property
    def schedule(self) -> str | None:
        return self.home_binding.schedule

    @schedule.setter
    def schedule(self, value: str | None) -> None:
        self.home_binding.schedule = value

    @property
    def dag_id(self) -> str | None:
        return self.home_binding.dag_id

    @dag_id.setter
    def dag_id(self, value: str | None) -> None:
        self.home_binding.dag_id = value

    @property
    def dag_file_path(self) -> str | None:
        return self.home_binding.dag_file_path

    @dag_file_path.setter
    def dag_file_path(self, value: str | None) -> None:
        self.home_binding.dag_file_path = value

    @property
    def status(self) -> "ProjectStatus":
        return self.home_binding.status

    @status.setter
    def status(self, value: "ProjectStatus") -> None:
        self.home_binding.status = value

    @property
    def active_version_id(self) -> int | None:
        return self.home_binding.active_version_id

    @active_version_id.setter
    def active_version_id(self, value: int | None) -> None:
        self.home_binding.active_version_id = value

    @property
    def environment(self):
        """Read-only — derived from the home binding, never itself stored or settable (a
        binding's environment is fixed at creation, see ProjectEnvironmentBinding.environment).
        §3.6's project-header badge reads this."""
        return self.home_binding.environment


class MedallionDataset(Base):
    __tablename__ = "medallion_datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    layer: Mapped[MedallionLayer] = mapped_column(Enum(MedallionLayer, name="medallion_layer"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # bronze-only
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"), nullable=True)
    source_object: Mapped[str | None] = mapped_column(String(255), nullable=True)
    load_mode: Mapped[LoadMode | None] = mapped_column(Enum(LoadMode, name="medallion_load_mode"), nullable=True)
    incremental_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    partition_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bronze_location: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # silver/gold-only
    dbt_model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    materialization: Mapped[Materialization | None] = mapped_column(Enum(Materialization, name="medallion_materialization"), nullable=True)
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)

    # gold-only Python/ML node (Module 4) — transform_type discriminates dbt vs python;
    # a dbt node ignores these, a python node ignores dbt_model_name/materialization/sql.
    transform_type: Mapped[TransformType] = mapped_column(Enum(TransformType, name="medallion_transform_type"), default=TransformType.dbt, nullable=False)
    ml_objective: Mapped[MLObjective] = mapped_column(Enum(MLObjective, name="medallion_ml_objective"), default=MLObjective.none, nullable=False)
    python_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("ml_templates.id", ondelete="SET NULL"), nullable=True)
    output_table: Mapped[str | None] = mapped_column(String(120), nullable=True)

    upstream_dataset_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    tests: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_status: Mapped[TestStatus] = mapped_column(Enum(TestStatus, name="medallion_test_status"), default=TestStatus.none, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MedallionRun(Base):
    __tablename__ = "medallion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    dag_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[RunState] = mapped_column(Enum(RunState, name="medallion_run_state"), default=RunState.queued, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    layer_stats: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    tests_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    logs_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MedallionVersion(Base):
    """Immutable snapshot of a project's deployed pipeline, captured at the end of a
    successful build (Module 8) — never edited or partially updated after creation."""
    __tablename__ = "medallion_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # artifacts already generated by build() — captured verbatim, never regenerated, so a
    # restore redeploys exactly what ran (dbt_project_snapshot's profiles.yml has its
    # credentials stripped; they're re-injected fresh from data_sources at restore time).
    dbt_project_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dag_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    datasets_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)

    dag_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dag_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_restore_of: Mapped[int | None] = mapped_column(ForeignKey("medallion_versions.id"), nullable=True)

    __table_args__ = (UniqueConstraint("project_id", "version_number", name="uq_medallion_version_project_number"),)


class MLTemplate(Base):
    """Seeded recipe (Module 4 étape 3) — an engineer clones one into a MedallionDataset's
    python_code, never edits the template row itself (admin-only)."""
    __tablename__ = "ml_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ml_objective: Mapped[MLObjective] = mapped_column(Enum(MLObjective, name="medallion_ml_objective"), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    python_code: Mapped[str] = mapped_column(Text, nullable=False)
    expected_inputs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    output_columns: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
