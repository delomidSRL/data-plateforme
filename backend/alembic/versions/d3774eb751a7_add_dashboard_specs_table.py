"""add dashboard_specs table

Revision ID: d3774eb751a7
Revises: 3c1c2c6e4b6e
Create Date: 2026-08-07 20:52:21.986483

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd3774eb751a7'
down_revision: Union[str, None] = '3c1c2c6e4b6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Note: autogenerate also proposed dropping several pre-existing, unrelated indexes
    # (data_quality_alerts/snapshots, medallion_versions, notification_channels) — drift from
    # an earlier migration, not something this revision should touch. Left out deliberately.
    op.create_table('dashboard_specs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('medallion_dataset_id', sa.Integer(), nullable=False),
    sa.Column('superset_instance_id', sa.Integer(), nullable=False),
    sa.Column('indicators', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('validated_by', sa.Integer(), nullable=False),
    sa.Column('last_generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['medallion_dataset_id'], ['medallion_datasets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['medallion_projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['superset_instance_id'], ['superset_instances.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['validated_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('medallion_dataset_id', 'superset_instance_id', name='uq_dashboard_spec_dataset_instance')
    )


def downgrade() -> None:
    op.drop_table('dashboard_specs')
