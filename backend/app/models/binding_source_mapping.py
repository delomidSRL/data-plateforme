"""Module 17 §5.2 — dev -> prod correspondence for a project's bronze ingestion sources
(the Oracle ERP, the client's MySQL, etc. — DataSource rows referenced by MedallionDataset.
source_id). The warehouse and object store are NOT here: those are direct fields of the
binding itself (§5.2). One row per distinct origin source the project's bronze datasets
reference, for the PROD binding specifically — a suggestion pre-filled by name/type match,
always confirmed by hand before the first promotion can proceed (§0: never a silent mapping)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class BindingSourceMapping(Base):
    __tablename__ = "binding_source_mappings"
    __table_args__ = (UniqueConstraint("binding_id", "origin_source_id", name="uq_binding_source_mapping"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    binding_id: Mapped[int] = mapped_column(ForeignKey("project_environment_bindings.id", ondelete="CASCADE"), nullable=False)
    origin_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    # Nullable until confirmed — a suggestion can pre-fill this, but `confirmed` alone gates
    # whether promotion may proceed (§5.3.3: "le déploiement reste bloqué tant qu'une source
    # bronze n'a pas de target_source_id confirmé").
    target_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
