"""Module 19 étape 1 — the project's persisted dbt workspace: one row per file on the dbt
project's tree. Previously, `dbt_project.generate_project_files` produced this content fresh
on every build and threw it away right after deployment (only Module 8's `MedallionVersion`
kept a frozen copy, and nobody could edit it). Now it is `project_files`'s own source of
truth: `services/workspace.py` is the only writer in this stage, mirroring exactly what the
generators already produce — no human edit path exists until étape 2, so `content` and
`base_content` always move together here."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ProjectFile(Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "path", name="uq_project_files_project_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # Relative to the dbt project root, e.g. "models/gold/mart_sales.sql" — never "profiles.yml"
    # (§2: no secret is ever persisted in the workspace).
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # The last version a generator produced — compared against `content_hash` to derive
    # `is_modified` (never stored: étape 1 never lets the two diverge, since no write endpoint
    # exists yet; étape 2 is what makes `base_content` stop tracking `content`).
    base_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "dbt_project | gold_builder | test_renderer | agent | none" — none of étape 1's files are
    # ever "none" (human-authored, no base) since no write path exists yet.
    generator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="SET NULL"), nullable=True, index=True)
    # Optimistic concurrency (§2) — every write must pass `if_version`; a stale write gets 409.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
