from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.superset_instance import SupersetInstanceOrigin, SupersetInstanceStatus


class SupersetInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    origin: SupersetInstanceOrigin
    stack_id: int | None = None
    base_url: str
    admin_username: str
    superset_version: str | None = None
    verify_tls: bool
    status: SupersetInstanceStatus
    last_checked_at: datetime | None = None
    is_active: bool
    created_at: datetime


class SupersetInstanceTestResult(BaseModel):
    reachable: bool
    message: str
    superset_version: str | None = None
    status: SupersetInstanceStatus
