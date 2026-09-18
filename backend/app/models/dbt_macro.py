"""New module — platform-wide, admin-defined dbt macros (UX ask: admin-only, like MLTemplate,
not project-scoped — a data engineer picks from the library, only an admin edits it).
Complements the fixed built-in catalogue (STRUCTURATION_MACROS in payload_structure.py, always
available) with macros an admin authors from a dedicated "Macro management" screen, offered
alongside built-ins in every project's 03_standardized SQL editor (same click-to-insert logic
as columns/table references) and written unconditionally into every generated dbt project's
macros/*.sql on every build."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class DbtMacro(Base):
    __tablename__ = "dbt_macros"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique platform-wide (not per-project): every generated dbt project gets every macro
    # unconditionally, and dbt itself would reject two same-named macro definitions anyway.
    # Must be a valid Jinja/dbt identifier, must match the `{% macro <name>(...) %}` declared
    # inside `definition` (checked in the route layer, not enforceable here), and must not
    # collide with a built-in macro name (checked against dbt_macros.BUILTIN_MACRO_NAMES).
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # UX ask — the admin writes real Jinja anyway, so no separate structured "parameters"
    # form: this is the COMPLETE macro definition, `{% macro name(...) -%} ... {%- endmacro %}`
    # included, exactly as it gets written into macros/<name>.sql (dbt_macros.render_macro).
    definition: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
