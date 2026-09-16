from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class SemanticAnnotation(Base):
    __tablename__ = "semantic_annotations"
    __table_args__ = (
        # Postgres treats NULL as distinct from NULL in a UNIQUE constraint, so this alone
        # would let table-level annotations (column_name IS NULL) duplicate freely — the
        # partial index below is what actually enforces "one table-level annotation per
        # (source, table)".
        UniqueConstraint("data_source_id", "table_name", "column_name", name="uq_semantic_annotation_source_table_column"),
        Index(
            "uq_semantic_annotation_source_table_null_column",
            "data_source_id", "table_name",
            unique=True,
            postgresql_where=text("column_name IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
