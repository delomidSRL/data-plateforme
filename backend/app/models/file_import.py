import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class FileImportFormat(str, enum.Enum):
    csv = "csv"
    excel = "excel"
    json = "json"
    xml = "xml"


class FileImportWriteMode(str, enum.Enum):
    create = "create"
    replace = "replace"
    append = "append"


class ImportMode(str, enum.Enum):
    """Module 6 extension (payload & structuration) — the choice made once, at import time,
    between deciding the schema now (`typed`, M6 étape 1, unchanged) and capturing the file
    faithfully first, structuring it later (`payload`, §3)."""
    typed = "typed"
    payload = "payload"


class FileImportStatus(str, enum.Enum):
    draft = "draft"
    awaiting_validation = "awaiting_validation"
    importing = "importing"
    imported = "imported"
    error = "error"


class FileImport(Base):
    """A one-off file → SQL table import (Module 6). Once imported, the resulting table in
    the `imports` schema is an ordinary source — Airflow/dbt never see the original file."""
    __tablename__ = "file_imports"
    __table_args__ = (Index("ix_file_imports_target", "target_source_id", "target_schema", "target_table"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    imported_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # fichier
    source_file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # archive (brut, avant tout parsing)
    archive_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True)
    archive_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # format
    format: Mapped[FileImportFormat] = mapped_column(Enum(FileImportFormat, name="file_import_format"), nullable=False)
    format_options: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # contrat de mapping (validé par l'utilisateur, persisté pour le réimport)
    column_mapping: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    contract_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # cible
    target_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True)
    target_schema: Mapped[str] = mapped_column(String(120), default="imports", nullable=False)
    target_table: Mapped[str | None] = mapped_column(String(120), nullable=True)
    write_mode: Mapped[FileImportWriteMode] = mapped_column(Enum(FileImportWriteMode, name="file_import_write_mode"), default=FileImportWriteMode.create, nullable=False)

    # resultat
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cast_errors: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # etat
    status: Mapped[FileImportStatus] = mapped_column(Enum(FileImportStatus, name="file_import_status"), default=FileImportStatus.draft, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Module 6 extension §3.2 — `typed` is the M6 étape 1 behavior, byte-for-byte unchanged.
    import_mode: Mapped[ImportMode] = mapped_column(Enum(ImportMode, name="file_import_mode"), default=ImportMode.typed, nullable=False)
