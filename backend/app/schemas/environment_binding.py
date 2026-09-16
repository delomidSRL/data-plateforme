from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    environment: str
    is_home: bool
    airflow_instance_id: int
    object_store_source_id: int
    warehouse_source_id: int
    schedule: str | None = None
    target: str
    dag_id: str | None = None
    dag_file_path: str | None = None
    status: str
    active_version_id: int | None = None
    created_at: datetime
