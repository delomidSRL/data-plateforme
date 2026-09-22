"""add dq_flag_registry_entries table

Revision ID: 7c2e491ba5d3
Revises: f15f4606a26e
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7c2e491ba5d3'
down_revision: Union[str, None] = 'f15f4606a26e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    category = postgresql.ENUM("informative", "elimination", name="dq_flag_category")

    op.create_table(
        "dq_flag_registry_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("flag_name", sa.String(64), nullable=False, unique=True),
        sa.Column("category", category, nullable=False, server_default="elimination"),
        sa.Column("source_rule", sa.String(255), nullable=True),
        sa.Column("issue_type", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("dq_flag_registry_entries")
    postgresql.ENUM(name="dq_flag_category").drop(op.get_bind())
