from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.medallion import MLObjective


class MLTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ml_objective: MLObjective
    description: str | None = None
    python_code: str = Field(min_length=1)
    expected_inputs: list[dict] = Field(default_factory=list)
    output_columns: list[str] = Field(default_factory=list)


class MLTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    ml_objective: MLObjective | None = None
    description: str | None = None
    python_code: str | None = Field(default=None, min_length=1)
    expected_inputs: list[dict] | None = None
    output_columns: list[str] | None = None


class MLTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ml_objective: MLObjective
    description: str | None = None
    python_code: str
    expected_inputs: list[dict]
    output_columns: list[str]
    created_at: datetime
