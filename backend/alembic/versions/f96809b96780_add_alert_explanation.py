"""add cached AI explanation to data quality alerts (module 16 etape 5)

Revision ID: f96809b96780
Revises: 6acc7e062bdf
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f96809b96780'
down_revision: Union[str, None] = '6acc7e062bdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("data_quality_alerts", sa.Column("explanation", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("data_quality_alerts", "explanation")
