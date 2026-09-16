import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class StackStatus(str, enum.Enum):
    draft = "draft"
    deploying = "deploying"
    running = "running"
    stopped = "stopped"
    error = "error"


class InfraStack(Base):
    __tablename__ = "infra_stacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    services: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    compose_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[StackStatus] = mapped_column(Enum(StackStatus, name="stack_status"), default=StackStatus.draft, nullable=False)
    last_deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
