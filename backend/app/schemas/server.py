from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.server import AuthMethod, Environment, ServerStatus


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hostname: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=120)
    auth_method: AuthMethod
    secret: str = Field(min_length=1, description="Mot de passe SSH ou clé privée, selon auth_method.")
    # Module 17 §3.2/§3.6 — obligatoire : "un serveur = un environnement" (v1 parti pris §0).
    environment: Environment


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, min_length=1, max_length=120)
    auth_method: AuthMethod | None = None
    secret: str | None = Field(default=None, min_length=1)
    environment: Environment | None = None


class ServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    hostname: str
    ssh_port: int
    ssh_user: str
    auth_method: AuthMethod
    status: ServerStatus
    docker_version: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime
    environment: Environment


class ServerTestResult(BaseModel):
    reachable: bool
    message: str
    docker_version: str | None = None
