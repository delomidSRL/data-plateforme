import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class DataSourceType(str, enum.Enum):
    postgresql = "postgresql"
    minio = "minio"
    oracle = "oracle"
    mysql = "mysql"


class DataSourceOrigin(str, enum.Enum):
    platform = "platform"
    external = "external"


class DataSourceStatus(str, enum.Enum):
    unknown = "unknown"
    reachable = "reachable"
    unreachable = "unreachable"


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[DataSourceType] = mapped_column(Enum(DataSourceType, name="data_source_type"), nullable=False)
    origin: Mapped[DataSourceOrigin] = mapped_column(Enum(DataSourceOrigin, name="data_source_origin"), default=DataSourceOrigin.external, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[DataSourceStatus] = mapped_column(Enum(DataSourceStatus, name="data_source_status"), default=DataSourceStatus.unknown, nullable=False)
    stack_id: Mapped[int | None] = mapped_column(ForeignKey("infra_stacks.id", ondelete="SET NULL"), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
