"""add quality flags registry to payload structuration (module 18)

Revision ID: 0e09c1262e33
Revises: 9ad68133a7a7
Create Date: 2026-09-17 18:58:00.335352

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0e09c1262e33'
down_revision: Union[str, None] = '9ad68133a7a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Module 18 — dataset-level quality-flag rules (03/04's no-code authoring), one list per
    # payload structuration contract; category (informative|elimination) on each rule is what
    # 05_validated/05_quarantine's routing and the rendered dq_flag_registry.csv seed read.
    op.add_column(
        "payload_structurations",
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("payload_structurations", "quality_flags")
