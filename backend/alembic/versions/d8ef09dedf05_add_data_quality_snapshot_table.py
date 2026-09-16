"""add data quality snapshot table

Revision ID: d8ef09dedf05
Revises: 7eb9a2dddd4c
Create Date: 2026-07-14 23:33:55.263981

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd8ef09dedf05'
down_revision: Union[str, None] = '7eb9a2dddd4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_quality_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("medallion_runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("tests_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tests_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_hash", sa.String(64), nullable=True),
        sa.Column("schema_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("dataset_id", "run_id", name="uq_quality_snapshot_dataset_run"),
    )
    op.create_index("ix_data_quality_snapshots_dataset_id", "data_quality_snapshots", ["dataset_id"])
    op.create_index("ix_data_quality_snapshots_project_id", "data_quality_snapshots", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_data_quality_snapshots_project_id", table_name="data_quality_snapshots")
    op.drop_index("ix_data_quality_snapshots_dataset_id", table_name="data_quality_snapshots")
    op.drop_table("data_quality_snapshots")
