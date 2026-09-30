"""Module 19 étape 3 §5.3 — a pending (or resolved) three-way merge outcome for one workspace
file. Created by `services/workspace_merge.apply_generated()` whenever a generator's fresh
output diverges from a human-modified file's own base; never for an intact file (those are
just updated in place, exactly like étape 1/2's materialize() already did)."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ConflictStatus(str, enum.Enum):
    # Clean three-way merge (`git merge-file` exit 0) — nothing written until the human
    # accepts (or edits via resolve) the proposal.
    proposed = "proposed"
    # Real conflict (`git merge-file` found overlapping hunks) — merged_content carries
    # <<<<<<</=======/>>>>>>> markers; the human must resolve before the build unblocks.
    open = "open"
    resolved = "resolved"
    # "Garder la version humaine" (§5.5) — content is untouched, only the base advances so
    # the same regeneration isn't re-proposed next time.
    discarded = "discarded"


class ConflictTrigger(str, enum.Enum):
    canvas = "canvas"
    agent = "agent"
    quality = "quality"
    gold_spec = "gold_spec"
    restore = "restore"


class ProjectFileConflict(Base):
    __tablename__ = "project_file_conflicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    base_content: Mapped[str] = mapped_column(Text, nullable=False)
    ours_content: Mapped[str] = mapped_column(Text, nullable=False)
    theirs_content: Mapped[str] = mapped_column(Text, nullable=False)
    merged_content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ConflictStatus] = mapped_column(Enum(ConflictStatus, name="project_file_conflict_status"), default=ConflictStatus.proposed, nullable=False)
    generator: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[ConflictTrigger] = mapped_column(Enum(ConflictTrigger, name="project_file_conflict_trigger"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
