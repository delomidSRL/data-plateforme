"""add medallion folders

Revision ID: c341b8bd916a
Revises: 757cbe1834a9
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c341b8bd916a'
down_revision: Union[str, None] = '757cbe1834a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'medallion_folders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_medallion_folders_owner_id', 'medallion_folders', ['owner_id'])
    # Module 15 §2.1 — one user can't have two folders with the same name, case-insensitive;
    # two different users can. DB-enforced (not just app-level), race-safe under concurrent
    # writes.
    op.create_index(
        'uq_medallion_folders_owner_name_lower', 'medallion_folders',
        ['owner_id', sa.text('lower(name)')], unique=True,
    )

    op.add_column('medallion_projects', sa.Column('folder_id', sa.Integer(), nullable=True))
    op.create_index('ix_medallion_projects_folder_id', 'medallion_projects', ['folder_id'])
    op.create_foreign_key(
        'fk_medallion_projects_folder_id', 'medallion_projects', 'medallion_folders',
        ['folder_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_medallion_projects_folder_id', 'medallion_projects', type_='foreignkey')
    op.drop_index('ix_medallion_projects_folder_id', table_name='medallion_projects')
    op.drop_column('medallion_projects', 'folder_id')

    op.drop_index('uq_medallion_folders_owner_name_lower', table_name='medallion_folders')
    op.drop_index('ix_medallion_folders_owner_id', table_name='medallion_folders')
    op.drop_table('medallion_folders')
