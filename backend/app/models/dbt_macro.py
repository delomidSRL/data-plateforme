"""New module — platform-wide, admin-defined dbt macros (UX ask: admin-only, like MLTemplate,
not project-scoped — a data engineer picks from the library, only an admin edits it).
Complements the fixed built-in catalogue (STRUCTURATION_MACROS in payload_structure.py, always
available) with macros an admin authors from a dedicated "Macro management" screen, offered
alongside built-ins in every project's 03_standardized SQL editor (same click-to-insert logic
as columns/table references) and written unconditionally into every generated dbt project's
macros/*.sql on every build."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class DbtMacro(Base):
    __tablename__ = "dbt_macros"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique platform-wide (not per-project): every generated dbt project gets every macro
    # unconditionally, and dbt itself would reject two same-named macro definitions anyway.
    # Must be a valid Jinja/dbt identifier and must not collide with a built-in macro name
    # (checked in the route layer against dbt_macros.BUILTIN_MACRO_NAMES, not enforceable here).
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ordered list of {"name": str, "default": str | None} — rendered as the macro's formal
    # parameter list; a non-null default renders as "name=default" in the {% macro %} signature.
    parameters: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Raw SQL/Jinja body only — the {% macro name(...) -%} / {%- endmacro %} wrapper is added
    # at render time (dbt_macros.render_macro), never stored here.
    sql_body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
