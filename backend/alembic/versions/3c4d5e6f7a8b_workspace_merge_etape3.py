"""Module 19 étape 3 — fusion à trois voies (project_file_conflicts)

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c4d5e6f7a8b'
down_revision: Union[str, None] = '2b3c4d5e6f7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_file_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("base_content", sa.Text(), nullable=False),
        sa.Column("ours_content", sa.Text(), nullable=False),
        sa.Column("theirs_content", sa.Text(), nullable=False),
        sa.Column("merged_content", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum("proposed", "open", "resolved", "discarded", name="project_file_conflict_status"), nullable=False, server_default="proposed"),
        sa.Column("generator", sa.String(32), nullable=False),
        sa.Column("trigger", sa.Enum("canvas", "agent", "quality", "gold_spec", "restore", name="project_file_conflict_trigger"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_project_file_conflicts_project_id", "project_file_conflicts", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_file_conflicts_project_id", table_name="project_file_conflicts")
    op.drop_table("project_file_conflicts")
    sa.Enum(name="project_file_conflict_status").drop(op.get_bind())
    sa.Enum(name="project_file_conflict_trigger").drop(op.get_bind())
