from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.data_quality import AlertSeverity, AlertStatus, AlertType, CheckSource, CheckStatus, CheckType, DbtTestSeverity, NotificationChannelType, QualityIndicator, QualityLayer, QualityMetricStatus


class QualitySnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    dataset_id: int
    project_id: int
    run_id: int | None = None
    collected_at: datetime
    loaded_at: datetime | None = None
    row_count: int | None = None
    tests_passed: int
    tests_failed: int
    schema_hash: str | None = None
    # aliased: "schema_json" collides with pydantic v1's deprecated BaseModel.schema_json()
    schema_columns: list[dict] = Field(validation_alias="schema_json", serialization_alias="schema_json")


class MetricOut(BaseModel):
    """Module 16 §4.2 — one intrinsic-quality result. `target_column` is "" (not None) for a
    table-level indicator (grain_uniqueness, join_loss, raw_duplicates) — surfaced as None to
    the frontend, the "" is purely a DB-uniqueness implementation detail (see the model)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    project_id: int
    layer: QualityLayer
    indicator: QualityIndicator
    target_column: str | None = None
    defect_rate: float | None = None
    score: float | None = None
    raw: dict
    status: QualityMetricStatus
    computed_at: datetime

    @classmethod
    def from_model(cls, m) -> "MetricOut":
        out = cls.model_validate(m)
        if out.target_column == "":
            out.target_column = None
        return out


class OverviewCellOut(BaseModel):
    """§5.2 — one target_column's real result behind a matrix cell (the "déplié" detail)."""
    target_column: str | None = None
    defect_rate: float | None = None
    score: float | None = None
    status: QualityMetricStatus
    raw: dict


class OverviewIndicatorOut(BaseModel):
    """§5.2 — one indicator's column in the matrix for one table: the WORST cell's
    score/status represents the whole indicator at a glance (e.g. null_rate's most-affected
    column), `cells` carries every underlying target_column for the expand."""
    indicator: QualityIndicator
    score: float | None = None
    defect_rate: float | None = None
    status: QualityMetricStatus
    cells: list[OverviewCellOut]


class OverviewTableOut(BaseModel):
    dataset_id: int
    dataset_name: str
    indicators: list[OverviewIndicatorOut]


class OverviewLayerOut(BaseModel):
    layer: QualityLayer
    composite_score: float | None = None
    tables: list[OverviewTableOut]


class QualityOverviewOut(BaseModel):
    """Module 16 §5.3 — GET /quality/overview. `run_id=None` with `layers=[]` means no
    intrinsic-quality metric has ever been collected for this project (§5.4.4: a Module-5-only
    project shows the Qualité tab exactly as before)."""
    project_id: int
    run_id: int | None = None
    layers: list[OverviewLayerOut]


class ExpectedSourceVolume(BaseModel):
    """§6.2 — a single ground-truth assertion (never a list: one reference volume per
    pipeline). `value` is deliberately optional even in a saved baseline: the AI bootstrap
    (§6.3) may propose a candidate `source_ref` (which bronze table this is about) without
    ever proposing `value` itself — asserting the true expected count is the engineer's job
    alone, never inferred from the data (§0's circularity rule)."""
    source_ref: str = Field(min_length=1, max_length=120)
    value: int | None = Field(default=None, ge=0)


class ConservativeMeasureAssertion(BaseModel):
    column: str = Field(min_length=1, max_length=255)


class RequiredDimensionAssertion(BaseModel):
    column: str = Field(min_length=1, max_length=255)


class BaselineAssertions(BaseModel):
    expected_source_volume: ExpectedSourceVolume | None = None
    conservative_measures: list[ConservativeMeasureAssertion] = Field(default_factory=list)
    required_dimensions: list[RequiredDimensionAssertion] = Field(default_factory=list)


class BaselineOut(BaseModel):
    project_id: int
    assertions: BaselineAssertions
    updated_at: datetime | None = None
    updated_by: int | None = None


class BaselineUpdate(BaseModel):
    assertions: BaselineAssertions


class BaselineSuggestionOut(BaseModel):
    """§6.3 — a non-persistent draft: POST /baseline/suggest never writes anything, the
    engineer reviews then PUTs their own (possibly edited, possibly empty) version. `rationale`
    is a short human-readable "why" per populated field, so the draft isn't a black box."""
    assertions: BaselineAssertions
    rationale: dict[str, str] = Field(default_factory=dict)


class CheckSuggestion(BaseModel):
    """§7.3/§7.5 — one non-persistent AI-proposed check, shown to the engineer with its
    rationale before anything is written (mirrors BaselineSuggestionOut's own draft/rationale
    pairing, §6.3)."""
    check_type: CheckType
    target_column: str | None = None
    parameters: dict
    rationale: str = ""


class CheckSuggestOut(BaseModel):
    suggestions: list[CheckSuggestion] = Field(default_factory=list)


class CheckCreate(BaseModel):
    """§7.4 — POST /quality/checks: an engineer validating an AI suggestion (source=ai_suggested)
    or authoring one from scratch (source=engineer) go through this same shape; both are held to
    the identical validate_check_parameters boundary (§7.6.4)."""
    dataset_id: int
    layer: QualityLayer
    check_type: CheckType
    target_column: str = ""
    parameters: dict
    source: CheckSource = CheckSource.engineer


class CheckUpdate(BaseModel):
    """§7.4 — PUT /{id}: activer/désactiver (status) and/or modifier ses paramètres (§7.5
    "modifie") — both optional, PUT-as-partial-update matching this module's own
    ProjectFolderUpdate/ChannelUpdate precedent rather than requiring every field re-sent.

    Module 16 extension §5 — `materialize_as_dbt_test`/`dbt_test_severity`: the tri-state
    « Contrôles » control (Observer seulement / Prévenir au build / Bloquer le build) writes
    through these two fields on the SAME check row, never a second declaration (§0)."""
    status: CheckStatus | None = None
    parameters: dict | None = None
    target_column: str | None = None
    materialize_as_dbt_test: bool | None = None
    dbt_test_severity: DbtTestSeverity | None = None


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    dataset_id: int | None = None
    layer: QualityLayer
    target_column: str | None = None
    check_type: CheckType
    parameters: dict
    source: CheckSource
    status: CheckStatus
    created_at: datetime
    materialize_as_dbt_test: bool
    dbt_test_severity: DbtTestSeverity
    # Module 16 extension §3/§5 — read-only: is this check_type in expectation_catalog at all
    # (drives the frontend's grisé/actif state), and the concrete dbt form once materialized
    # (JetBrains Mono display) — both computed, never stored twice.
    materializable: bool = False
    dbt_form: str | None = None

    @classmethod
    def from_model(cls, m) -> "CheckOut":
        from app.services import dbt_test_renderer, expectation_catalog

        out = cls.model_validate(m)
        if out.target_column == "":
            out.target_column = None
        out.materializable = expectation_catalog.is_materializable(m.check_type)
        if m.materialize_as_dbt_test:
            out.dbt_form = dbt_test_renderer.describe(m)
        return out


class SchemaDiffEntry(BaseModel):
    column: str
    change: str  # added | removed | retyped
    old_type: str | None = None
    new_type: str | None = None


class DatasetQualityOut(BaseModel):
    dataset_id: int
    dataset_name: str
    latest: QualitySnapshotOut | None = None
    trend: list[QualitySnapshotOut] = Field(default_factory=list)
    volume_variation_pct: float | None = None
    schema_changed: bool = False
    schema_diff: list[SchemaDiffEntry] = Field(default_factory=list)


class ProjectQualityOut(BaseModel):
    project_id: int
    datasets: list[DatasetQualityOut]


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    volume_variation_pct: int
    min_rows: int
    freshness_max_hours: int
    alert_on_test_failure: bool
    alert_on_schema_change: bool
    # Module 16 §8.2 (Étape 5) — coherence-alert layer thresholds.
    orphan_rate_max: float
    join_loss_max_pct: float
    validity_min_pct: float
    reconciliation_tolerance_pct: float
    plausibility_max_pct: float
    layer_weights: dict | None = None


class RuleUpdate(BaseModel):
    volume_variation_pct: int = Field(ge=1, le=100)
    min_rows: int = Field(ge=0)
    freshness_max_hours: int = Field(ge=1)
    alert_on_test_failure: bool
    alert_on_schema_change: bool
    orphan_rate_max: float = Field(ge=0, le=1)
    join_loss_max_pct: float = Field(ge=0, le=100)
    validity_min_pct: float = Field(ge=0, le=100)
    reconciliation_tolerance_pct: float = Field(ge=0, le=100)
    plausibility_max_pct: float = Field(ge=0, le=100)
    layer_weights: dict | None = None


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    project_id: int
    snapshot_id: int
    type: AlertType
    severity: AlertSeverity
    message: str
    status: AlertStatus
    created_at: datetime
    explanation: str | None = None


class ChannelCreate(BaseModel):
    type: NotificationChannelType
    config: dict  # plaintext: {"recipients": [...]} for email, {"url": ..., "secret": ...} for webhook
    enabled: bool = True
    min_severity: AlertSeverity = AlertSeverity.warning


class ChannelUpdate(BaseModel):
    config: dict | None = None
    enabled: bool | None = None
    min_severity: AlertSeverity | None = None


class ChannelOut(BaseModel):
    id: int
    project_id: int | None = None
    type: NotificationChannelType
    config: dict
    enabled: bool
    min_severity: AlertSeverity
    created_at: datetime
