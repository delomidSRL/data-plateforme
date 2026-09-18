"""add dbt_macros table

Revision ID: ad9b86a3f45a
Revises: 0e09c1262e33
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ad9b86a3f45a'
down_revision: Union[str, None] = '0e09c1262e33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New module — project-scoped, user-authored dbt macros, offered alongside the fixed
    # STRUCTURATION_MACROS built-ins in the 03_standardized SQL editor and rendered into
    # macros/*.sql on every build (see app.services.payload_structure.render_custom_macro).
    op.create_table('dbt_macros',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
    sa.Column('sql_body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['medallion_projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'name', name='uq_dbt_macro_project_name'),
    )


def downgrade() -> None:
    op.drop_table('dbt_macros')
