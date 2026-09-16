"""add superset_publications table

Revision ID: c120f6733e01
Revises: e9b7cb867cc9
Create Date: 2026-08-06 01:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c120f6733e01'
down_revision: Union[str, None] = 'e9b7cb867cc9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'superset_publications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('medallion_dataset_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('superset_instance_id', sa.Integer(), nullable=False),
        sa.Column('superset_database_id', sa.Integer(), nullable=False),
        sa.Column('superset_dataset_id', sa.Integer(), nullable=False),
        sa.Column('superset_chart_id', sa.Integer(), nullable=True),
        sa.Column('superset_dashboard_id', sa.Integer(), nullable=True),
        sa.Column('published_url', sa.String(length=1000), nullable=False),
        sa.Column('published_by', sa.Integer(), nullable=False),
        sa.Column('last_published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['medallion_dataset_id'], ['medallion_datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['medallion_projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['superset_instance_id'], ['superset_instances.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['published_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('medallion_dataset_id', 'superset_instance_id', name='uq_superset_publication_dataset_instance'),
    )


def downgrade() -> None:
    op.drop_table('superset_publications')
