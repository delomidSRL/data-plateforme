from pydantic import BaseModel, ConfigDict, Field

from app.models.medallion import ProjectTarget


class ProdBindingUpsert(BaseModel):
    airflow_instance_id: int
    warehouse_source_id: int
    object_store_source_id: int
    schedule: str | None = None
    target: ProjectTarget = ProjectTarget.prod


class SourceMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    origin_source_id: int
    origin_name: str
    origin_type: str
    target_source_id: int | None = None
    target_name: str | None = None
    confirmed: bool


class SourceMappingConfirm(BaseModel):
    target_source_id: int


class DiffOut(BaseModel):
    datasets_added: list[str]
    datasets_removed: list[str]
    sql_changed: list[dict]
    tests_changed: list[dict]


class AlertBriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    severity: str
    message: str


class PromotionPreviewOut(BaseModel):
    dev_deployed: bool
    dev_version_number: int | None = None
    prod_configured: bool
    prod_version_number: int | None = None
    diff: DiffOut | None = None
    mappings: list[SourceMappingOut]
    mapping_complete: bool
    blocking_alerts: list[AlertBriefOut]
    advisory_alerts: list[AlertBriefOut]
    can_promote: bool


class PromoteRequest(BaseModel):
    confirm: bool = Field(default=False)
