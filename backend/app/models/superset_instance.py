import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class SupersetInstanceOrigin(str, enum.Enum):
    platform = "platform"
    external = "external"


class SupersetInstanceStatus(str, enum.Enum):
    unknown = "unknown"
    reachable = "reachable"
    unreachable = "unreachable"
    unsupported_version = "unsupported_version"
    bad_credentials = "bad_credentials"


class SupersetInstance(Base):
    """Superset promoted to a first-class entity (Module 11), same origin (platform|external)
    pattern as AirflowInstance/DataSource. `platform` instances are auto-registered from an
    InfraStack's Superset service (Module 1) — read-only params, lifecycle tied to the stack.
    `external` is prepared but unused in v1 (no CRUD route yet)."""
    __tablename__ = "superset_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    origin: Mapped[SupersetInstanceOrigin] = mapped_column(Enum(SupersetInstanceOrigin, name="superset_instance_origin"), default=SupersetInstanceOrigin.external, nullable=False)
    stack_id: Mapped[int | None] = mapped_column(ForeignKey("infra_stacks.id", ondelete="SET NULL"), nullable=True)

    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    admin_username: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    superset_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    status: Mapped[SupersetInstanceStatus] = mapped_column(Enum(SupersetInstanceStatus, name="superset_instance_status"), default=SupersetInstanceStatus.unknown, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # platform only: false after the owning stack's `down` (never deleted, so any future FK
    # to it never dangles across a redeploy cycle) — mirrors AirflowInstance.is_active.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
