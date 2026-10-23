"""env switcher: per-binding run tag and dataset stats

Revision ID: ae104399803a
Revises: c3d4e5f6a7b8
Create Date: 2026-10-23 14:36:09.414422

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ae104399803a'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only this migration's own changes — autogenerate also picked up a pile of unrelated
    # pre-existing drift (an old `dq_flag_registry_entries` drop, several stale index drops
    # on data_quality_*/medallion_versions/notification_channels) that has nothing to do with
    # this feature and must NOT be applied here (that table has real, populated data).
    op.create_table(
        "medallion_dataset_binding_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("last_row_count", sa.Integer(), nullable=True),
        sa.Column("last_loaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", postgresql.ENUM("none", "passed", "failed", name="medallion_test_status", create_type=False), nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["project_environment_bindings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["medallion_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "binding_id", name="uq_dataset_binding_stats"),
    )
    op.add_column("medallion_runs", sa.Column("binding_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_medallion_runs_binding_id", "medallion_runs", "project_environment_bindings", ["binding_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("fk_medallion_runs_binding_id", "medallion_runs", type_="foreignkey")
    op.drop_column("medallion_runs", "binding_id")
    op.drop_table("medallion_dataset_binding_stats")
