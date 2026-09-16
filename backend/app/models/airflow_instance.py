import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AirflowInstanceOrigin(str, enum.Enum):
    platform = "platform"
    external = "external"


class AirflowInstanceStatus(str, enum.Enum):
    unknown = "unknown"
    reachable = "reachable"
    unreachable = "unreachable"
    unsupported_version = "unsupported_version"


class AirflowInstance(Base):
    """Airflow becomes a first-class entity, same origin (platform|external) pattern as
    DataSource. `platform` instances are auto-registered from an InfraStack's Airflow
    service (Module 1) — read-only params, lifecycle tied to the stack. `external`
    instances are declared by the user (URL + credentials), full CRUD (admin)."""
    __tablename__ = "airflow_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    origin: Mapped[AirflowInstanceOrigin] = mapped_column(Enum(AirflowInstanceOrigin, name="airflow_instance_origin"), default=AirflowInstanceOrigin.external, nullable=False)
    stack_id: Mapped[int | None] = mapped_column(ForeignKey("infra_stacks.id", ondelete="SET NULL"), nullable=True)

    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_version: Mapped[str] = mapped_column(String(10), default="v2", nullable=False)
    airflow_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Deployment (step 2, optional) — SSH/SFTP access for depositing DAG + dbt project files.
    # {ssh_host, ssh_port, ssh_user, auth_method, secret_encrypted, dags_path, dbt_path}
    # nullable = pilotable but not deployable yet.
    deploy_access: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Last preflight result: {deployable: bool, checks: {...}, checked_at}
    capabilities: Mapped[dict] = mapped_column(JSONB, default=lambda: {"deployable": False}, nullable=False)

    status: Mapped[AirflowInstanceStatus] = mapped_column(Enum(AirflowInstanceStatus, name="airflow_instance_status"), default=AirflowInstanceStatus.unknown, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # platform only: false after the owning stack's `down` (never deleted, so
    # medallion_projects.airflow_instance_id never dangles across a redeploy cycle).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
