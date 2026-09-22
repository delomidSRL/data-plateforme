"""Platform-wide, admin-managed registry of data-quality flag names (revives Module 18 §8's
dq_flag_registry.csv seed, now decoupled from the no-code quality_flags contract that used to
feed it — 04_annotated/05_validated/05_quarantine are hand-written SQL now, and any dataset's
SQL can `{{ ref('dq_flag_registry') }}` this table to look up a flag's `category`. Mirrors
DbtMacro's admin-managed, platform-wide pattern exactly."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class DqFlagCategory(str, enum.Enum):
    informative = "informative"
    elimination = "elimination"


class DqFlagRegistryEntry(Base):
    __tablename__ = "dq_flag_registry_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique platform-wide — one row per flag name, exactly like the old CSV's dedup-by-name
    # rule ("quelle que soit la table"), just enforced at write time instead of at render time.
    flag_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The ONLY column a hand-written 05_validated/05_quarantine's routing actually reads.
    category: Mapped[DqFlagCategory] = mapped_column(Enum(DqFlagCategory, name="dq_flag_category"), default=DqFlagCategory.elimination, nullable=False)
    # Free-text, purely descriptive — never read by any routing logic (same as the old CSV's
    # source_rule/issue_type columns).
    source_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issue_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
