from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TargetType = Literal["text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"]


class ColumnMappingEntry(BaseModel):
    source_name: str
    target_name: str
    target_type: TargetType
    format: str | None = None
    include: bool = True
    nullable: bool = True
    # produced by inference, kept for traceability — not required on the way back in
    inferred_type: str | None = None
    confidence: float | None = None
    ambiguous: bool = False
    sample: list[str] = Field(default_factory=list)


class FileImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    imported_by: int | None = None
    created_at: datetime

    source_file_name: str
    file_size: int
    checksum: str | None = None
    uploaded_at: datetime | None = None

    archive_source_id: int | None = None
    archive_path: str | None = None

    format: str
    format_options: dict

    column_mapping: list[ColumnMappingEntry]
    contract_hash: str | None = None

    target_source_id: int | None = None
    target_schema: str
    target_table: str | None = None
    write_mode: str

    row_count: int | None = None
    cast_errors: dict
    imported_at: datetime | None = None

    status: str
    last_error: str | None = None
    import_mode: str = "typed"


class ImportFromObjectStoreCreate(BaseModel):
    """A file already sitting in an object-store bucket (browsed from a medallion project's
    canvas) instead of one freshly uploaded — everything past "fetch these bytes" is the exact
    same file-import machinery (payload vs typed, write mode, ...) as the regular Imports
    wizard. `source_id` is the MinIO source to fetch from; the same source also archives the
    bytes and the project's own warehouse is always the target — no separate pickers needed,
    unlike the standalone Imports page which isn't tied to any one project."""
    source_id: int
    bucket: str
    key: str
    format: Literal["csv", "excel"] = "csv"
    format_options: dict = Field(default_factory=dict)
    name: str | None = None
    import_mode: Literal["typed", "payload"] = "typed"
    write_mode: Literal["create", "replace", "append"] = "create"


class ObjectStoreColumnsRequest(BaseModel):
    """Scratch analysis only, no FileImport row — mirrors POST /api/imports/columns (used by
    the standalone wizard's source_pk candidate list) but for a file already sitting in a
    bucket instead of one just uploaded."""
    source_id: int
    bucket: str
    key: str
    format: Literal["csv", "excel"] = "csv"
    format_options: dict = Field(default_factory=dict)


class FileImportUpdate(BaseModel):
    column_mapping: list[ColumnMappingEntry]
    target_table: str = Field(min_length=1, max_length=120)
    write_mode: Literal["create", "replace", "append"] = "create"


class FileImportStatusOut(BaseModel):
    status: str
    row_count: int | None = None
    cast_errors: dict
    last_error: str | None = None


class XmlCandidateOut(BaseModel):
    xpath: str
    count: int


class XmlCandidatesOut(BaseModel):
    candidates: list[XmlCandidateOut]


class ColumnsOut(BaseModel):
    columns: list[str]
