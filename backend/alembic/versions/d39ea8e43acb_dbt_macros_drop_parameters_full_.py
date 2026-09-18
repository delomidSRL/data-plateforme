"""dbt_macros: drop parameters, sql_body becomes the full definition

Revision ID: d39ea8e43acb
Revises: 27f42e13dc2c
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd39ea8e43acb'
down_revision: Union[str, None] = '27f42e13dc2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # UX ask — no separate structured "parameters" form: an admin writes the whole
    # `{% macro name(...) -%} ... {%- endmacro %}` block by hand, so `sql_body` (previously
    # just the inner body) becomes `definition` (the complete block) and `parameters` is gone.
    # Any macro rows created under the previous shape are deleted first — this module only just
    # shipped, so this is a bounded, safe reset, not a rewrite of real data.
    op.execute("DELETE FROM dbt_macros")
    op.drop_column("dbt_macros", "parameters")
    op.alter_column("dbt_macros", "sql_body", new_column_name="definition")


def downgrade() -> None:
    op.alter_column("dbt_macros", "definition", new_column_name="sql_body")
    op.add_column("dbt_macros", sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"))
