import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class AuthMethod(str, enum.Enum):
    key = "key"
    password = "password"


class ServerStatus(str, enum.Enum):
    unknown = "unknown"
    reachable = "reachable"
    unreachable = "unreachable"


class Environment(str, enum.Enum):
    """Module 17 — a server carries one environment tag; a project's binding to a given
    environment resolves to whichever stack/sources live on a server tagged the same way
    (v1 parti pris §0: "un serveur = un environnement"). Defined here since the server is
    the tag's primary owner; reused as-is by ProjectEnvironmentBinding."""
    dev = "dev"
    prod = "prod"


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    ssh_user: Mapped[str] = mapped_column(String(120), nullable=False)
    auth_method: Mapped[AuthMethod] = mapped_column(Enum(AuthMethod, name="server_auth_method"), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[ServerStatus] = mapped_column(Enum(ServerStatus, name="server_status"), default=ServerStatus.unknown, nullable=False)
    docker_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    environment: Mapped[Environment] = mapped_column(Enum(Environment, name="server_environment"), nullable=False)
