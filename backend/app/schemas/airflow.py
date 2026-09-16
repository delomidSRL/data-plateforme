from datetime import datetime

from pydantic import BaseModel, Field


class ConnectionCreateRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=200)
    conn_type: str
    from_data_source_id: int | None = None
    host: str | None = None
    login: str | None = None
    password: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    port: int | None = None
    extra: str | None = None
    description: str | None = None

    model_config = {"populate_by_name": True}


class ConnectionTestRequest(BaseModel):
    conn_type: str
    host: str | None = None
    login: str | None = None
    password: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    port: int | None = None
    extra: str | None = None

    model_config = {"populate_by_name": True}


class DagDeployRequest(BaseModel):
    filename: str = Field(min_length=1)
    content: str = Field(min_length=1)


class DagPauseRequest(BaseModel):
    is_paused: bool


class DagRunTriggerRequest(BaseModel):
    logical_date: datetime | None = None
    conf: dict = Field(default_factory=dict)
