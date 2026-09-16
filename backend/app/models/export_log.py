"""Module 11 extension — export audit trail. One row per export of gold-layer data
(CSV today, whole dbt project later — one generalized table, `kind ∈ {csv, dbt_project}`,
never two). Written best-effort at stream open and it never blocks the download (§3.6/§5).

The exported data itself never lands here — only *who* exported *which* table, *when*, and
*from where*. The control-plane invariant "never stores the raw data" holds: the rows
transit through `COPY … TO STDOUT`, only this metadata is persisted (§0/§5)."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ExportKind(str, enum.Enum):
    csv = "csv"
    dbt_project = "dbt_project"


class ExportLog(Base):
    __tablename__ = "export_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: a dataset (or a user) can be deleted long after an export — the
    # audit line must survive it. `dataset_id` is also nullable by design: a `dbt_project`
    # export is project-scoped, not dataset-scoped.
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("medallion_datasets.id", ondelete="SET NULL"), nullable=True
    )
    exported_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[ExportKind] = mapped_column(Enum(ExportKind, name="export_log_kind"), nullable=False)
    # Left NULL at stream open (unknown without cost); filled best-effort once the COPY
    # finishes and libpq reports the row count (§4).
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
