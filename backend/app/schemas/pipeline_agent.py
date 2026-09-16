from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.pipeline_plan import PipelinePlanStatus


class MapIntentRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)


class MappingColumnsOut(BaseModel):
    measure: list[str] = []
    temporal: list[str] = []
    dimension: list[str] = []
    join_keys: list[str] = []


class FromProfileOut(BaseModel):
    row_count: int | None = None
    temporal_range: list[str] | None = None
    null_rate_key: float | None = None


class EvidenceOut(BaseModel):
    """Module 14 §5.2 — from_model is the AI's own (structurally-validated) argumentation;
    from_profile is the deterministic anchor computed by source_profile.py."""
    from_model: list[str] = []
    from_profile: FromProfileOut = FromProfileOut()


class MappingTableOut(BaseModel):
    table: str
    source_id: int
    role: str
    confidence: float
    columns: MappingColumnsOut
    evidence: EvidenceOut | None = None
    # Module 14 extension "primitives gold" §3.1 — real, complete distinct values per column
    # (only columns with one), the source pipeline_plan.py validates a gold filter's value
    # against.
    filter_enum_values: dict[str, list[str]] = {}


class CandidateOut(BaseModel):
    table: str
    source_id: int


class UnresolvedNeedOut(BaseModel):
    need: str
    candidates: list[CandidateOut] = []


class RelationshipOut(BaseModel):
    """Module 14 §4 — a backend-computed FK candidate between two columns on the same data
    source (never AI output). Correctif "fiabilisation détection de relations" (§6): `verified`
    is the only state ever injected into the AI prompt — a candidate with `verified=False`
    carries `rejected_reason`/`rejected_reason_label` so the UI can show it greyed out with why."""
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    child: str
    parent: str
    match_rate: float
    distinct_child: int
    distinct_parent: int
    confidence: float
    basis: str
    verified: bool = True
    name_affinity: float | None = None
    parent_unique_ratio: float | None = None
    from_introspected_fk: bool = False
    rejected_reason: str | None = None
    rejected_reason_label: str | None = None


class MappingResultOut(BaseModel):
    status: str
    tables: list[MappingTableOut] = []
    unresolved: list[UnresolvedNeedOut] = []
    relationships: list[RelationshipOut] = []


class BronzePlanOut(BaseModel):
    name: str
    table: str
    source_id: int
    mode: str
    incremental_key: str | None = None


class LogicalJoinOut(BaseModel):
    left: str
    right: str
    type: str


class LogicalPlanOut(BaseModel):
    """Module 14 §6.2 — the silver's intent in a closed vocabulary, produced before (and
    checked against) its sql."""
    joins: list[LogicalJoinOut] = []
    filters: list[str] = []
    normalizations: list[str] = []
    output: list[str] = []


class SilverPlanOut(BaseModel):
    name: str
    upstreams: list[str] = []
    sql: str
    rationale: str = ""
    status: str
    approved_by: int | None = None
    approved_at: str | None = None
    logical_plan: LogicalPlanOut | None = None
    # Non-blocking (§6.2/§12): a join that doesn't match a high-confidence detected
    # relationship (Module 14 §4) between the same two tables — the engineer decides.
    logical_plan_warning: str | None = None


class GoldPlanOut(BaseModel):
    name: str
    upstreams: list[str] = []
    metric_column: str
    aggregation: str
    # Module 14 extension "primitives gold" §3 — additive/optional: a gold using none of these
    # is the original flat contract, unchanged.
    metric: dict | None = None
    filters: list[dict] = []
    numerator: dict | None = None
    denominator: dict | None = None
    scale: float | None = None
    dimension_columns: list[str] = []
    time_column: str | None = None
    time_grain: str | None = None
    sql: str
    # Module 14 §5.3 — grain: derived from the same inputs as the gold's own GROUP BY, never a
    # second AI-declared value. grain_warning: non-blocking (the engineer resolves it), set
    # when the instruction explicitly names a granularity that differs from this gold's own.
    grain: list[str] = []
    grain_warning: str | None = None


class TestPlanOut(BaseModel):
    dataset: str
    column: str
    test: str
    include: bool = True
    values: list[str] = []
    to: str | None = None
    field: str | None = None


class RepairLogEntryOut(BaseModel):
    """Module 14 §7 — one entry per silver the repair loop touched (silvers that validated on
    the first try never appear here)."""
    name: str
    attempts: int
    status: str  # "repaired" | "failed"
    error: str | None = None


class PlanOut(BaseModel):
    bronze: list[BronzePlanOut] = []
    silver: list[SilverPlanOut] = []
    gold: list[GoldPlanOut] = []
    tests: list[TestPlanOut] = []
    repair_log: list[RepairLogEntryOut] = []
    # Annexe "élargir le scope de l'assistant IA" §plan-wide — advisory only (never blocks
    # validation): too many golds proposed, or a business need named in the instruction with
    # no matching column anywhere across the proposed golds.
    gold_warnings: list[str] = []


class ReplanRequest(BaseModel):
    confirm: bool = False


class DashboardIndicatorIn(BaseModel):
    title: str
    viz_type: str
    metric_column: str
    aggregation: str
    dimension_columns: list[str] = []
    time_column: str | None = None
    # Annexe catalogue viz §7 — carries the multi-slot contract through untouched for any
    # type beyond the original 5 (build_chart_payload dispatches on this, not the flat
    # fields above, whenever it's set); None for heuristic-sourced/legacy indicators.
    slots: dict[str, dict] | None = None
    # Top/Bottom N — {"limit": int, "direction": "top"|"bottom"}, only for supports_ranking
    # types (table/ag_grid today). AI-proposed only; None otherwise.
    ranking: dict | None = None


class DashboardDatasetIn(BaseModel):
    dataset_id: int
    indicators: list[DashboardIndicatorIn] = []


class GenerateAgentDashboardsRequest(BaseModel):
    """The engineer's validated/edited version of execution_state.dashboard_review, submitted
    to POST /agent/dashboard/generate to actually build the Superset charts."""
    datasets: list[DashboardDatasetIn] = []


class SilverEdit(BaseModel):
    name: str
    sql: str | None = None
    upstreams: list[str] | None = None
    approve: bool | None = None


class PlanPatch(BaseModel):
    remove_bronze: list[str] = Field(default_factory=list)
    remove_silver: list[str] = Field(default_factory=list)
    remove_gold: list[str] = Field(default_factory=list)
    silver_edits: list[SilverEdit] = Field(default_factory=list)
    test_toggles: dict[str, bool] = Field(default_factory=dict)


class PipelinePlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    instruction: str
    mapping: MappingResultOut | None = None
    plan: PlanOut | None = None
    status: PipelinePlanStatus
    execution_state: dict
    created_by: int
    created_at: datetime
    updated_at: datetime
    # Annexe "élargir le scope de l'assistant IA" §debug — every AI call this request made
    # (prompt + raw response, or the error if it failed), for the browser DevTools. Only
    # populated by routes that call ai_client.reset_debug_log()/get_debug_log(); None
    # otherwise (e.g. GET /plan, which never calls the AI) — never persisted, request-scoped.
    ai_debug: list[dict] | None = None
