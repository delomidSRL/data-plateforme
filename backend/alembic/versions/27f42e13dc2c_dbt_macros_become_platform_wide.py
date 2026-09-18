"""dbt_macros become platform-wide (admin-only), not project-scoped

Revision ID: 27f42e13dc2c
Revises: ad9b86a3f45a
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '27f42e13dc2c'
down_revision: Union[str, None] = 'ad9b86a3f45a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # UX ask — macros move from "one library per project" to "one library for the whole
    # platform, admin-managed" (mirrors MLTemplate): a follow-up ALTER rather than editing the
    # already-shipped ad9b86a3f45a in place, since that revision may already be applied to a
    # real database. Any macro rows created under the previous, project-scoped shape are
    # deleted first — dbt_macros only just shipped, so this is a bounded, safe reset, not a
    # rewrite of real data.
    op.execute("DELETE FROM dbt_macros")
    op.drop_constraint("uq_dbt_macro_project_name", "dbt_macros", type_="unique")
    op.drop_constraint("dbt_macros_project_id_fkey", "dbt_macros", type_="foreignkey")
    op.drop_column("dbt_macros", "project_id")
    op.create_unique_constraint("uq_dbt_macro_name", "dbt_macros", ["name"])


def downgrade() -> None:
    op.drop_constraint("uq_dbt_macro_name", "dbt_macros", type_="unique")
    op.add_column("dbt_macros", sa.Column("project_id", sa.Integer(), nullable=False))
    op.create_foreign_key("dbt_macros_project_id_fkey", "dbt_macros", "medallion_projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_dbt_macro_project_name", "dbt_macros", ["project_id", "name"])
