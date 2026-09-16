"""module 17 etape 3 - binding source mappings (dev -> prod bronze source correspondence)

Revision ID: a29d6e0157f4
Revises: f3a8c1d92b47
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a29d6e0157f4'
down_revision: Union[str, None] = 'f3a8c1d92b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "binding_source_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("binding_id", sa.Integer(), sa.ForeignKey("project_environment_bindings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("target_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("binding_id", "origin_source_id", name="uq_binding_source_mapping"),
    )


def downgrade() -> None:
    op.drop_table("binding_source_mappings")
