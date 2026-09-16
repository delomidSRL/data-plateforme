from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.data_source import DataSourceOrigin, DataSourceStatus, DataSourceType


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: DataSourceType
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str | None = None
    username: str = Field(min_length=1, max_length=255)
    secret: str = Field(min_length=1, description="Mot de passe ou secret key selon le type.")
    options: dict = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = None
    username: str | None = Field(default=None, min_length=1, max_length=255)
    secret: str | None = Field(default=None, min_length=1)
    options: dict | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: DataSourceType
    origin: DataSourceOrigin
    host: str
    port: int
    database_name: str | None = None
    username: str
    options: dict
    status: DataSourceStatus
    stack_id: int | None = None
    last_tested_at: datetime | None = None
    created_at: datetime


class SourceTestRequest(BaseModel):
    type: DataSourceType
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str | None = None
    username: str = Field(min_length=1, max_length=255)
    secret: str = Field(min_length=1)
    options: dict = Field(default_factory=dict)


class SourceTestResult(BaseModel):
    reachable: bool
    message: str
    latency_ms: int | None = None
    version: str | None = None


class ProvenanceOut(BaseModel):
    type: str = "file_import"
    import_id: int
    file: str
    imported_at: datetime | None = None
    row_count: int | None = None


class TableInfoOut(BaseModel):
    name: str
    provenance: ProvenanceOut | None = None


class SchemaInfoOut(BaseModel):
    name: str
    tables: list[TableInfoOut]


class SourceIntrospectResult(BaseModel):
    schemas: list[SchemaInfoOut] = []
    buckets: list[str] = []
    objects: list[str] = []
