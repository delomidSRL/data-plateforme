"""New module — project-scoped, user-defined dbt macros. Complements the fixed built-in
catalogue (STRUCTURATION_MACROS in payload_structure.py, always available) with macros a
project's own users author from the UI, offered alongside built-ins in the 03_standardized
SQL editor (same click-to-insert logic as columns/table references) and written into the
generated dbt project's macros/*.sql on every build."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class DbtMacro(Base):
    __tablename__ = "dbt_macros"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_dbt_macro_project_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    # Must be a valid Jinja/dbt identifier and must not collide with a built-in macro name
    # (checked in the service layer against STRUCTURATION_MACROS.keys(), not enforceable here).
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ordered list of {"name": str, "default": str | None} — rendered as the macro's formal
    # parameter list; a non-null default renders as "name=default" in the {% macro %} signature.
    parameters: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Raw SQL/Jinja body only — the {% macro name(...) -%} / {%- endmacro %} wrapper is added
    # at render time (render_custom_macro in payload_structure.py), never stored here.
    sql_body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
