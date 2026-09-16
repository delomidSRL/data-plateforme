from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.airflow_instance import AirflowInstanceOrigin, AirflowInstanceStatus


class AirflowInstanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    verify_tls: bool = True


class AirflowInstanceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    username: str | None = Field(default=None, min_length=1, max_length=255)
    password: str | None = None  # blank/absent = keep current secret
    verify_tls: bool | None = None


class DeployAccessOut(BaseModel):
    """Non-secret fields only — `secret_encrypted` is never serialized to the frontend.
    Extra keys on the source dict (like secret_encrypted) are silently dropped by Pydantic
    when validating into this narrower model, which is exactly the point."""
    ssh_host: str
    ssh_port: int
    ssh_user: str
    auth_method: str
    dags_path: str
    dbt_path: str
    dbt_bin: str = "dbt"
    exec_prefix: str = ""


class AirflowInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    origin: AirflowInstanceOrigin
    stack_id: int | None = None
    base_url: str
    api_version: str
    airflow_version: str | None = None
    username: str
    verify_tls: bool
    deploy_access: DeployAccessOut | None = None
    capabilities: dict
    status: AirflowInstanceStatus
    last_checked_at: datetime | None = None
    is_active: bool
    created_at: datetime


class AirflowInstanceTestRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    verify_tls: bool = True


class AirflowInstanceTestResult(BaseModel):
    reachable: bool
    message: str
    airflow_version: str | None = None
    status: AirflowInstanceStatus


class DagOut(BaseModel):
    dag_id: str
    is_paused: bool
    managed: bool


class DeployAccessUpdate(BaseModel):
    ssh_host: str = Field(min_length=1, max_length=255)
    ssh_port: int = 22
    ssh_user: str = Field(min_length=1, max_length=120)
    auth_method: str = Field(pattern="^(key|password)$")
    secret: str | None = None  # blank/absent on update = keep current
    dags_path: str = Field(min_length=1, max_length=500)
    dbt_path: str = Field(min_length=1, max_length=500)
    dbt_bin: str = Field(default="dbt", max_length=500)
    # Prefix to reach the *actual* runtime Airflow executes tasks in — e.g. a container
    # ("docker exec <worker> ") when SSH lands on a bare host next to a dockerized Airflow.
    # Empty means "run directly in the SSH session" (bare-metal / same-host Airflow).
    exec_prefix: str = Field(default="", max_length=500)


class PreflightCheckOut(BaseModel):
    key: str
    label: str
    passed: bool
    # Structured instead of a pre-formatted string so the frontend can render the
    # message template in the active UI language, interpolating the real diagnostic
    # values (paths, versions, raw command output/errors) which stay as-is either way.
    message_key: str
    message_params: dict


class PreflightResultOut(BaseModel):
    deployable: bool
    checks: list[PreflightCheckOut]
    checked_at: datetime | None = None
    stale: bool = False
