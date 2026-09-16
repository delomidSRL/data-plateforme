"""add superset_instances table

Revision ID: e9b7cb867cc9
Revises: a6c97a985567
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e9b7cb867cc9'
down_revision: Union[str, None] = 'a6c97a985567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'superset_instances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('origin', sa.Enum('platform', 'external', name='superset_instance_origin'), nullable=False),
        sa.Column('stack_id', sa.Integer(), nullable=True),
        sa.Column('base_url', sa.String(length=500), nullable=False),
        sa.Column('admin_username', sa.String(length=255), nullable=False),
        sa.Column('secret_encrypted', sa.String(), nullable=False),
        sa.Column('superset_version', sa.String(length=30), nullable=True),
        sa.Column('verify_tls', sa.Boolean(), nullable=False),
        sa.Column('status', sa.Enum('unknown', 'reachable', 'unreachable', 'unsupported_version', 'bad_credentials', name='superset_instance_status'), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['stack_id'], ['infra_stacks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('superset_instances')
    op.execute("DROP TYPE IF EXISTS superset_instance_origin")
    op.execute("DROP TYPE IF EXISTS superset_instance_status")
