import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, BigInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AlertType(str, enum.Enum):
    volume = "volume"
    freshness = "freshness"
    tests = "tests"
    schema = "schema"
    # Module 16 §8.2 (Étape 5) — intrinsic-quality alert types, added to THIS SAME enum/table
    # rather than a parallel one (§8.2: "Aucune table d'alertes nouvelle — c'est ce qui rend
    # l'unification propre plutôt que cosmétique"). integrity=referential_integrity,
    # validity=type_conformity/format_validity, consistency=intra_row_consistency,
    # reconciliation=aggregate_reconciliation/end_to_end_conservation, plausibility=plausibility,
    # completeness=ingestion_completeness/dimensional_completeness/conditional_completeness.
    integrity = "integrity"
    validity = "validity"
    consistency = "consistency"
    reconciliation = "reconciliation"
    plausibility = "plausibility"
    completeness = "completeness"


class QualityLayer(str, enum.Enum):
    bronze = "bronze"
    silver = "silver"
    gold = "gold"


class QualityIndicator(str, enum.Enum):
    """Module 16 §3 — the closed catalogue of intrinsic-quality indicators. Every one of
    these is computed deterministically (SQL); the AI never calculates a value, only
    proposes checks/baselines upstream or explains a measured defect downstream (§0)."""
    # bronze — fidélité d'ingestion (§3.1)
    ingestion_completeness = "ingestion_completeness"  # ⟨baseline⟩
    parsing_rejection_rate = "parsing_rejection_rate"
    type_conformity = "type_conformity"  # ⟨contrat⟩
    null_rate = "null_rate"
    raw_duplicates = "raw_duplicates"
    # silver — intégrité des transformations (§3.2)
    referential_integrity = "referential_integrity"
    join_loss = "join_loss"
    grain_uniqueness = "grain_uniqueness"
    format_validity = "format_validity"  # ⟨contrat⟩
    intra_row_consistency = "intra_row_consistency"  # ⟨contrat⟩
    categorical_normalization = "categorical_normalization"
    completeness_gain = "completeness_gain"
    # gold — cohérence de restitution (§3.3)
    aggregate_reconciliation = "aggregate_reconciliation"  # ⟨baseline⟩
    end_to_end_conservation = "end_to_end_conservation"  # ⟨baseline⟩
    dimensional_completeness = "dimensional_completeness"  # ⟨baseline⟩
    conditional_completeness = "conditional_completeness"  # ⟨contrat⟩
    plausibility = "plausibility"  # ⟨contrat⟩


class QualityMetricStatus(str, enum.Enum):
    ok = "ok"
    warning = "warning"
    critical = "critical"
    skipped = "skipped"


class AlertSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class AlertStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"


class NotificationChannelType(str, enum.Enum):
    email = "email"
    webhook = "webhook"


class DataQualitySnapshot(Base):
    """One row per (dataset, run) — the historized copy of MedallionDataset's
    always-overwritten last_row_count/last_loaded_at/last_test_status runtime fields."""
    __tablename__ = "data_quality_snapshots"
    __table_args__ = (UniqueConstraint("dataset_id", "run_id", name="uq_quality_snapshot_dataset_run"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("medallion_runs.id", ondelete="CASCADE"), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tests_passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tests_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


class DataQualityMetric(Base):
    """Module 16 §4.2/§13 — a CHILD table of DataQualitySnapshot (M5's own parent row stays
    structurally unchanged), one row per (snapshot, dataset, indicator, target_column). Table
    fille rather than widening the snapshot: the count of intrinsic-quality results is
    variable (per column, per relation, per check) unlike M5's 4 fixed signals."""
    __tablename__ = "data_quality_metrics"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "dataset_id", "indicator", "target_column", name="uq_quality_metric"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("data_quality_snapshots.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    layer: Mapped[QualityLayer] = mapped_column(Enum(QualityLayer, name="data_quality_layer"), nullable=False)
    indicator: Mapped[QualityIndicator] = mapped_column(Enum(QualityIndicator, name="data_quality_indicator"), nullable=False)
    # NOT NULL with a "" default rather than nullable — Postgres treats NULL as distinct in a
    # unique constraint (two NULLs never conflict), which would silently break idempotence for
    # every table-level indicator (join_loss, grain_uniqueness, raw_duplicates...) that has no
    # single target column. "" uniformly means "no specific column" instead.
    target_column: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    defect_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # numerator/denominator and any other raw counters behind defect_rate, e.g.
    # {"orphans": 1200, "total": 100000} — what a human diagnostic or the AI explainer (§8.3)
    # reads, never re-derived from defect_rate alone.
    raw: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[QualityMetricStatus] = mapped_column(Enum(QualityMetricStatus, name="data_quality_metric_status"), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataQualityBaseline(Base):
    """Module 16 §6.2 — a ground-truth assertion declared by the engineer, one per pipeline
    (§13: "légèreté de saisie du global, précision d'évaluation du fin"). Never derived from
    the data itself (§0: inferring it from the data would be circular — a source already
    amputed would pass as conform). `assertions` holds up to 3 independently-optional blocks
    (expected_source_volume, conservative_measures, required_dimensions), each implicitly
    anchored to the layer its own indicator needs (bronze/end-to-end/gold respectively) —
    the engineer never reasons layer-by-layer."""
    __tablename__ = "data_quality_baselines"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, unique=True)
    assertions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class CheckType(str, enum.Enum):
    """Module 16 §7 — the closed catalogue of ⟨contrat⟩ indicators a DataQualityCheck can
    parameterize, exactly the 5 flagged ⟨contrat⟩ in QualityIndicator (§3)."""
    type_conformity = "type_conformity"
    format_validity = "format_validity"
    intra_row_consistency = "intra_row_consistency"
    plausibility = "plausibility"
    conditional_completeness = "conditional_completeness"


class CheckSource(str, enum.Enum):
    ai_suggested = "ai_suggested"
    engineer = "engineer"


class CheckStatus(str, enum.Enum):
    proposed = "proposed"
    active = "active"
    dismissed = "dismissed"


class DbtTestSeverity(str, enum.Enum):
    """Module 16 extension §5 — severity of the OPT-IN materialized dbt test. `warn` is the
    safe default: it never fails a build, preserving M16's own invariant for the observation
    path. `error` is a per-check, explicit engineer choice to make dbt build fail on this
    check's violation."""
    warn = "warn"
    error = "error"


class DataQualityCheck(Base):
    """Module 16 §7.2 — a parameterized quality contract check: the AI proposes (reading
    schema/stats/semantics only, §2), the engineer validates, `quality_intrinsic.collect()`
    evaluates every `active` check at each run (§7.3). `dataset_id` nullable = project-wide
    scope (rare in practice — most checks target one dataset/column)."""
    __tablename__ = "data_quality_checks"
    __table_args__ = (
        # Functional index (mirrors MedallionFolder's lower(name) precedent, models/medallion.py)
        # rather than a plain UniqueConstraint: `parameters` is JSONB (not directly comparable
        # in a btree unique constraint) and `dataset_id` is nullable (NULL != NULL would let
        # duplicate project-wide checks slip past a naive constraint) — both are coalesced/
        # hashed into the expression instead.
        Index(
            "uq_quality_checks_identity",
            "check_type", "target_column",
            text("coalesce(dataset_id, -1)"),
            text("md5(parameters::text)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=True)
    layer: Mapped[QualityLayer] = mapped_column(Enum(QualityLayer, name="data_quality_layer", create_type=False), nullable=False)
    # "" (not NULL) for the same idempotence reason as DataQualityMetric.target_column above —
    # conditional_completeness's real target lives inside `parameters.target` instead (its
    # "predicate" and "target column" are two different fields), so this stays "" for it.
    target_column: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    check_type: Mapped[CheckType] = mapped_column(Enum(CheckType, name="data_quality_check_type"), nullable=False)
    # e.g. {"target_type": "email"} (type_conformity), {"pattern": "^[^@]+@[^@]+$"} (format_
    # validity), {"predicate": "date_fin >= date_debut"} (intra_row_consistency), {"min": 0,
    # "max": 120} (plausibility), {"condition": "statut = 'facture'", "target": "montant"}
    # (conditional_completeness) — shape enforced by Pydantic per check_type, never free-form.
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source: Mapped[CheckSource] = mapped_column(Enum(CheckSource, name="data_quality_check_source"), nullable=False)
    status: Mapped[CheckStatus] = mapped_column(Enum(CheckStatus, name="data_quality_check_status"), default=CheckStatus.proposed, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Module 16 extension §5 — opt-in materialization as a dbt test (second, preventive
    # execution backend on this SAME check row — never a second declaration, §0). Safe
    # defaults ⇒ zero regression: every check pre-existing this extension stays observation-only.
    materialize_as_dbt_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dbt_test_severity: Mapped[DbtTestSeverity] = mapped_column(
        Enum(DbtTestSeverity, name="data_quality_dbt_test_severity"), default=DbtTestSeverity.warn, nullable=False
    )


class DataQualityRule(Base):
    """Threshold rules for a project (one row per project — safe defaults, no config needed)."""
    __tablename__ = "data_quality_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, unique=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=True)
    volume_variation_pct: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    min_rows: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    freshness_max_hours: Mapped[int] = mapped_column(Integer, default=26, nullable=False)
    alert_on_test_failure: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_on_schema_change: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Module 16 §8.2 (Étape 5) — layer thresholds for the ⟨coherence⟩ alert types above. Safe
    # defaults (§8.2), same "one row per project, no config needed" convention as the M5 fields
    # already here. orphan_rate_max is a defect-RATE ceiling (0 = zero tolerance, matches
    # quality_intrinsic._ZERO_TOLERANCE's own reasoning for referential_integrity/grain).
    orphan_rate_max: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    join_loss_max_pct: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    validity_min_pct: Mapped[float] = mapped_column(Float, default=95.0, nullable=False)
    reconciliation_tolerance_pct: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    plausibility_max_pct: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    # §3.4 — nullable = uniform weights (the current read-time default in quality.py's
    # _build_overview); an engineer/AI-suggested override lives here, e.g.
    # {"gold": {"aggregate_reconciliation": 0.4, ...}}, keyed by layer then indicator.
    layer_weights: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataQualityAlert(Base):
    __tablename__ = "data_quality_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("data_quality_snapshots.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[AlertType] = mapped_column(Enum(AlertType, name="data_quality_alert_type"), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity, name="data_quality_alert_severity"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus, name="data_quality_alert_status"), default=AlertStatus.open, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Module 16 §8.3/§8.4 — cached quality_contract.explain_alert() result: nullable (never
    # computed unless the engineer expands it), generated once and reused (§2 idempotence —
    # "une suggestion IA rejouée ne réappelle pas le LLM si le contrat existe déjà").
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=True)
    type: Mapped[NotificationChannelType] = mapped_column(Enum(NotificationChannelType, name="notification_channel_type"), nullable=False)
    config_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity, name="data_quality_alert_severity", create_type=False), default=AlertSeverity.warning, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
