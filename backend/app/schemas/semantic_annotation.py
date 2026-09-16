from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnnotationUpsert(BaseModel):
    table_name: str = Field(min_length=1, max_length=255)
    column_name: str | None = Field(default=None, max_length=255)
    description: str = Field(min_length=1, max_length=2000)


class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    data_source_id: int
    table_name: str
    column_name: str | None = None
    description: str
    created_by: int
    created_at: datetime
    updated_at: datetime


class ColumnInfoOut(BaseModel):
    name: str
    type: str
