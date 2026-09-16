"""add superset_instance_id to medallion_projects

Revision ID: 3c1c2c6e4b6e
Revises: c120f6733e01
Create Date: 2026-08-06 02:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3c1c2c6e4b6e'
down_revision: Union[str, None] = 'c120f6733e01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('medallion_projects', sa.Column('superset_instance_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_medallion_projects_superset_instance_id', 'medallion_projects', 'superset_instances',
        ['superset_instance_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_medallion_projects_superset_instance_id', 'medallion_projects', type_='foreignkey')
    op.drop_column('medallion_projects', 'superset_instance_id')
