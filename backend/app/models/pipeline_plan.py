import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class PipelinePlanStatus(str, enum.Enum):
    mapping = "mapping"
    planned = "planned"
    executing = "executing"
    waiting_approval = "waiting_approval"
    done = "done"
    mapping_failed = "mapping_failed"
    plan_failed = "plan_failed"
    run_failed = "run_failed"


class PipelinePlan(Base):
    """The Module 13 agent's persisted contract for one project — same role as
    `dashboard_specs` for Module 12: it (not Mistral's raw output) is what gets executed,
    and rejouer/reprendre a plan never re-calls Mistral. One active plan per project."""
    __tablename__ = "pipeline_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, unique=True)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    mapping: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[PipelinePlanStatus] = mapped_column(Enum(PipelinePlanStatus, name="pipeline_plan_status"), nullable=False)
    execution_state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
