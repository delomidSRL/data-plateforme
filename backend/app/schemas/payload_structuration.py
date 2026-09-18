from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TargetType = Literal["text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"]

# Module 18 §6.3/§8 — the closed catalog of no-code quality-flag rule types (04), each backed
# by a generic macro. `category` is the ONLY thing 05_validated/05_quarantine's routing reads —
# never the rule_type or params — exactly mirroring dq_flag_registry.csv's own schema.
FlagRuleType = Literal["format", "placeholder", "garbage", "date_range"]
FlagCategory = Literal["informative", "elimination"]


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


class QualityFlagRule(BaseModel):
    name: str
    field: str  # target_name (post-03) this rule reads
    rule_type: FlagRuleType
    category: FlagCategory = "elimination"
    # rule_type == "format"
    regex: str | None = None
    # rule_type == "date_range" (YYYY-MM-DD, either bound optional)
    min_date: str | None = None
    max_date: str | None = None
    # optional free-text traceability label (this platform's equivalent of an ORG-NR-xxx id)
    source_rule: str | None = None


class StructurationOut(BaseModel):
    dataset_id: int
    payload_column: str
    column_mapping: list[StructurationFieldEntry]
    quality_flags: list[QualityFlagRule] = Field(default_factory=list)
    contract_hash: str | None = None
    updated_at: datetime
    updated_by: int | None = None


class StructurationUpdate(BaseModel):
    column_mapping: list[StructurationFieldEntry]
    quality_flags: list[QualityFlagRule] = Field(default_factory=list)


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
