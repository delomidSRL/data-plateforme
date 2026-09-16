from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.infra_stack import StackStatus


class PostgresConfig(BaseModel):
    enabled: bool = False
    port: int = 5432
    user: str = "dataplateforme"
    password: str | None = None
    db_name: str = "dataplateforme"


class MinioConfig(BaseModel):
    enabled: bool = False
    api_port: int = 9000
    console_port: int = 9001
    access_key: str = "dataplateforme"
    secret_key: str | None = None


class SupersetConfig(BaseModel):
    enabled: bool = False
    port: int = 8088
    admin_user: str = "admin"
    admin_password: str | None = None
    secret_key: str | None = None


class AirflowConfig(BaseModel):
    enabled: bool = False
    web_port: int = 8080
    admin_user: str = "airflow"
    admin_password: str | None = None
    load_examples: bool = False
    enable_flower: bool = False
    dbt_enabled: bool = True
    fernet_key: str | None = None  # auto-generated server-side, never user-supplied
    jwt_secret: str | None = None  # auto-generated server-side, never user-supplied
    webserver_secret_key: str | None = None  # auto-generated server-side, never user-supplied


class JupyterConfig(BaseModel):
    enabled: bool = False
    port: int = 8888
    token: str | None = None  # auto-generated server-side if left blank
    mount_dbt: bool = True  # mount the Airflow stack's dbt/ dir read-only, if Airflow is enabled


class StackServices(BaseModel):
    postgres: PostgresConfig | None = None
    minio: MinioConfig | None = None
    superset: SupersetConfig | None = None
    airflow: AirflowConfig | None = None
    jupyter: JupyterConfig | None = None


class StackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    services: StackServices


class StackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    name: str
    services: dict
    status: StackStatus
    last_deployed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime


class StackPreview(BaseModel):
    compose_yaml: str
    warnings: list[str] = []


class StackDeployReport(BaseModel):
    compose_written: bool
    containers: list[str] = []
    registered_sources: list[str] = []


class ServiceCheck(BaseModel):
    name: str
    service: str
    state: str
    health: str | None = None
    ok: bool
    detail: str


class StackVerifyReport(BaseModel):
    ok: bool
    status: StackStatus
    services: list[ServiceCheck]
