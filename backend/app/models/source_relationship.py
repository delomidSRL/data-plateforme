from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class SourceRelationship(Base):
    """Module 14 §4.4 — a recalculable CACHE of detected FK-like relationships between two
    columns on the SAME data source (candidates, never a source of truth), attached to the
    `data_source` exactly like `SemanticAnnotation` so it's reused across every project built
    on that source. Losing this table has no consequence: `relationship_detect.py` recomputes
    it on demand. `schema_fingerprint` lets a stale row (computed against a schema that has
    since changed) be told apart from a fresh one without deleting anything."""
    __tablename__ = "source_relationships"
    __table_args__ = (
        UniqueConstraint(
            "data_source_id", "child_table", "child_column", "parent_table", "parent_column",
            name="uq_source_relationship_pair",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False)
    child_table: Mapped[str] = mapped_column(String(255), nullable=False)
    child_column: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_table: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_column: Mapped[str] = mapped_column(String(255), nullable=False)
    match_rate: Mapped[float] = mapped_column(Float, nullable=False)
    distinct_child: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_parent: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    basis: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Module 14 correctif §6 — additive, recalculable observability fields: a candidate is
    # only ever promoted to "fact injectable au prompt" (relationship_detect.compute_
    # relationships' return value) when verified=True. Every candidate is still persisted here
    # regardless (verified or not) so the UI can show WHY a rejected one was rejected.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    name_affinity: Mapped[float | None] = mapped_column(Float, nullable=True)
    parent_unique_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    from_introspected_fk: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    rejected_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
