"""drop payload_structurations.quality_flags

Revision ID: f15f4606a26e
Revises: d39ea8e43acb
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f15f4606a26e'
down_revision: Union[str, None] = 'd39ea8e43acb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 04_annotated/05_validated/05_quarantine are no longer auto-rendered from a no-code
    # quality-flags contract — they're now hand-written silver datasets, created via the "+" on
    # 03_standardized/04_annotated canvas nodes, same as 03_standardized itself already was.
    # Nothing reads this column any more.
    op.drop_column("payload_structurations", "quality_flags")


def downgrade() -> None:
    op.add_column(
        "payload_structurations",
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
