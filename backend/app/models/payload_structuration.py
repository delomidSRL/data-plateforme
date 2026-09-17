"""Module 6 extension (payload & structuration) §4.2 — the deterministic contract that turns
a bronze payload (JSONB, landed faithfully in `imports.<table>`) into typed columns. Rattaché
au MedallionDataset bronze (decision §11.3), not the FileImport — structuration is a médaillon
concern (lineage, run, ownership), not an import-time one."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class QuarantinePolicy(str, enum.Enum):
    report = "report"
    block = "block"


class PayloadStructuration(Base):
    __tablename__ = "payload_structurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # One contract per bronze dataset (§4.2 "Unicité (dataset_id)") — CASCADE: the contract is
    # meaningless once its dataset is gone, unlike ExportLog's audit trail.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    payload_column: Mapped[str] = mapped_column(String(120), default="payload", nullable=False)
    # Same shape as FileImport.column_mapping (source_name/target_name/target_type/format/
    # include/nullable/is_primary_key/inferred_type/confidence/ambiguous/sample).
    column_mapping: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # §5 rewrite (unpacked/typed convention) dropped the quarantine relation — every row from
    # bronze reaches 02_typed, diagnosed via cast_issues, never excluded — so these two are no
    # longer read or written anywhere. Left in place (harmless, SQLAlchemy-defaulted) rather
    # than an Alembic migration to drop them for a purely cosmetic cleanup.
    quarantine_policy: Mapped[QuarantinePolicy] = mapped_column(Enum(QuarantinePolicy, name="structuration_quarantine_policy"), default=QuarantinePolicy.report, nullable=False)
    quarantine_threshold_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    contract_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
