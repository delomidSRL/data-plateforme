from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TargetType = Literal["text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"]
OnCastError = Literal["quarantine", "null", "text"]


class StructurationFieldEntry(BaseModel):
    source_name: str
    target_name: str
    target_type: TargetType
    format: str | None = None
    include: bool = True
    nullable: bool = True
    is_primary_key: bool = False
    on_cast_error: OnCastError = "quarantine"
    # produced by inference, kept for traceability — not required on the way back in
    inferred_type: str | None = None
    confidence: float | None = None
    ambiguous: bool = False
    sample: list[str] = Field(default_factory=list)


class StructurationOut(BaseModel):
    dataset_id: int
    payload_column: str
    column_mapping: list[StructurationFieldEntry]
    quarantine_policy: str
    quarantine_threshold_pct: float | None = None
    contract_hash: str | None = None
    updated_at: datetime
    updated_by: int | None = None


class StructurationUpdate(BaseModel):
    column_mapping: list[StructurationFieldEntry]
    quarantine_policy: Literal["report", "block"] = "report"
    quarantine_threshold_pct: float | None = Field(default=None, ge=0, le=100)


class QuarantineRowOut(BaseModel):
    row_number: int | None = None
    source_file: str | None = None
    failures: dict
    payload: dict


class QuarantineSummaryEntry(BaseModel):
    column: str
    count: int
    sample_values: list[str] = Field(default_factory=list)


class QuarantineSummaryOut(BaseModel):
    total_quarantined: int
    by_column: list[QuarantineSummaryEntry]
