"""add relationship gate fields to source_relationships

Revision ID: 757cbe1834a9
Revises: 8ff94c4a95d9
Create Date: 2026-08-18 00:05:46.593057

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '757cbe1834a9'
down_revision: Union[str, None] = '8ff94c4a95d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('source_relationships', sa.Column('verified', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('source_relationships', sa.Column('name_affinity', sa.Float(), nullable=True))
    op.add_column('source_relationships', sa.Column('parent_unique_ratio', sa.Float(), nullable=True))
    op.add_column('source_relationships', sa.Column('from_introspected_fk', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('source_relationships', sa.Column('rejected_reason', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('source_relationships', 'rejected_reason')
    op.drop_column('source_relationships', 'from_introspected_fk')
    op.drop_column('source_relationships', 'parent_unique_ratio')
    op.drop_column('source_relationships', 'name_affinity')
    op.drop_column('source_relationships', 'verified')
