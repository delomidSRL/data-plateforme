from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TargetType = Literal["text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"]


class StructurationFieldEntry(BaseModel):
    source_name: str
    target_name: str
    target_type: TargetType
    format: str | None = None
    include: bool = True
    nullable: bool = True
    is_primary_key: bool = False
    # produced by inference, kept for traceability — not required on the way back in
    inferred_type: str | None = None
    confidence: float | None = None
    ambiguous: bool = False
    sample: list[str] = Field(default_factory=list)


class StructurationOut(BaseModel):
    dataset_id: int
    payload_column: str
    column_mapping: list[StructurationFieldEntry]
    contract_hash: str | None = None
    updated_at: datetime
    updated_by: int | None = None


class StructurationUpdate(BaseModel):
    column_mapping: list[StructurationFieldEntry]


class QuarantineRowOut(BaseModel):
    row_number: int | None = None
    source_file: str | None = None
    issues: list[str] = Field(default_factory=list)


class QuarantineSummaryEntry(BaseModel):
    column: str
    count: int
    sample_values: list[str] = Field(default_factory=list)


class QuarantineSummaryOut(BaseModel):
    total_quarantined: int
    by_column: list[QuarantineSummaryEntry]
