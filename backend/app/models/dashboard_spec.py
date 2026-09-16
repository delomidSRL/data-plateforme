from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class DashboardSpec(Base):
    """The validated indicator contract (Module 12 étape 2/3) — persisted so "régénérer"
    replays it deterministically without ever re-calling Mistral. One contract per
    (dataset, instance), same pairing as SupersetPublication."""
    __tablename__ = "dashboard_specs"
    __table_args__ = (UniqueConstraint("medallion_dataset_id", "superset_instance_id", name="uq_dashboard_spec_dataset_instance"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    medallion_dataset_id: Mapped[int] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False)
    superset_instance_id: Mapped[int] = mapped_column(ForeignKey("superset_instances.id", ondelete="CASCADE"), nullable=False)

    indicators: Mapped[list] = mapped_column(JSONB, nullable=False)  # [{title, viz_type, metric_column, aggregation, dimension_columns, time_column, slots}, ...]
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # ai | heuristic | manual
    # Annexe catalogue viz §6/§8 — "flat" for anything persisted before this annexe (indicator
    # dicts have only the legacy fields, no `slots` key at all); "slots" once written by the
    # catalogue-aware generation path. Additive, non-destructive: an old "flat" row still
    # reads back correctly via IndicatorSpec's own defaults (superset_publish.py) — this
    # column is a queryable marker, not a requirement for correct regeneration.
    contract_version: Mapped[str] = mapped_column(String(10), nullable=False, server_default="flat")
    validated_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    last_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
