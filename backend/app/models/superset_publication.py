from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class SupersetPublication(Base):
    """Idempotent trace of the link between a MedallionDataset and its Superset objects
    (Module 11) — persisted rather than reconciled-by-name on every call, so the canvas'
    "Publié" badge and direct link never need to round-trip Superset just to render, and
    republishing knows exactly which objects to reuse instead of re-deriving names."""
    __tablename__ = "superset_publications"
    __table_args__ = (UniqueConstraint("medallion_dataset_id", "superset_instance_id", name="uq_superset_publication_dataset_instance"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    medallion_dataset_id: Mapped[int] = mapped_column(ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    superset_instance_id: Mapped[int] = mapped_column(ForeignKey("superset_instances.id", ondelete="CASCADE"), nullable=False)

    superset_database_id: Mapped[int] = mapped_column(Integer, nullable=False)
    superset_dataset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    superset_chart_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    superset_dashboard_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    published_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    last_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
