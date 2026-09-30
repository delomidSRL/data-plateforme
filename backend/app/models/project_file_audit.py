"""Module 19 §2/§7.4 — "toute écriture [dans l'espace de travail] est réservée au propriétaire
et journalisée" is a non-negotiable transverse rule from étape 2 onward, not just étape 5's own
audit *viewer*. This table is written on every workspace write starting in étape 2; the "Code"
tab's own per-file history UI (§7.4's own scope) comes later — the rows are already there to
read once it does."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class ProjectFileAuditAction(str, enum.Enum):
    create = "create"
    update = "update"
    move = "move"
    delete = "delete"
    # Reserved for étape 3 (workspace_merge) — not written by anything in étape 2.
    accept_merge = "accept_merge"
    resolve = "resolve"
    discard = "discard"


class ProjectFileAudit(Base):
    __tablename__ = "project_file_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    action: Mapped[ProjectFileAuditAction] = mapped_column(Enum(ProjectFileAuditAction, name="project_file_audit_action"), nullable=False)
    # The human who wrote it, XOR the generator that did (étape 3 onward — always the human
    # in étape 2, since no generator writes through workspace_merge yet).
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    generator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Hashes only — never content (§7.4 "jamais de contenu de données").
    content_hash_before: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
