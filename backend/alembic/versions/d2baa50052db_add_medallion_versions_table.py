"""add medallion versions table

Revision ID: d2baa50052db
Revises: ed9eb95bc0ef
Create Date: 2026-07-25 23:15:48.478138

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd2baa50052db'
down_revision: Union[str, None] = 'ed9eb95bc0ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "medallion_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("dbt_project_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("dag_snapshot", sa.Text(), nullable=False),
        sa.Column("datasets_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("dag_id", sa.String(200), nullable=True),
        sa.Column("dag_file_path", sa.String(500), nullable=True),
        sa.Column("is_restore_of", sa.Integer(), sa.ForeignKey("medallion_versions.id"), nullable=True),
        sa.UniqueConstraint("project_id", "version_number", name="uq_medallion_version_project_number"),
    )
    op.create_index("ix_medallion_versions_project_id", "medallion_versions", ["project_id"])

    # active_version_id references medallion_versions, so it can only be added once that
    # table exists — nullable with no default: existing projects stay unversioned until
    # their next deploy, exactly the "never deployed = no history" rule (§3.2).
    op.add_column("medallion_projects", sa.Column("active_version_id", sa.Integer(), sa.ForeignKey("medallion_versions.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("medallion_projects", "active_version_id")
    op.drop_index("ix_medallion_versions_project_id", table_name="medallion_versions")
    op.drop_table("medallion_versions")
