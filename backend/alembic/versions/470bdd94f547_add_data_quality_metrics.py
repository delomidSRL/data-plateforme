"""add data quality metrics (module 16 etape 1)

Revision ID: 470bdd94f547
Revises: c341b8bd916a
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '470bdd94f547'
down_revision: Union[str, None] = 'c341b8bd916a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    layer = postgresql.ENUM("bronze", "silver", "gold", name="data_quality_layer")
    indicator = postgresql.ENUM(
        "ingestion_completeness", "parsing_rejection_rate", "type_conformity", "null_rate", "raw_duplicates",
        "referential_integrity", "join_loss", "grain_uniqueness", "format_validity", "intra_row_consistency",
        "categorical_normalization", "completeness_gain",
        "aggregate_reconciliation", "end_to_end_conservation", "dimensional_completeness",
        "conditional_completeness", "plausibility",
        name="data_quality_indicator",
    )
    metric_status = postgresql.ENUM("ok", "warning", "critical", "skipped", name="data_quality_metric_status")

    op.create_table(
        "data_quality_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("data_quality_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("layer", layer, nullable=False),
        sa.Column("indicator", indicator, nullable=False),
        sa.Column("target_column", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("defect_rate", sa.Float(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", metric_status, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_data_quality_metrics_snapshot_id", "data_quality_metrics", ["snapshot_id"])
    op.create_index("ix_data_quality_metrics_dataset_id", "data_quality_metrics", ["dataset_id"])
    op.create_index("ix_data_quality_metrics_project_id", "data_quality_metrics", ["project_id"])
    op.create_unique_constraint("uq_quality_metric", "data_quality_metrics", ["snapshot_id", "dataset_id", "indicator", "target_column"])


def downgrade() -> None:
    op.drop_constraint("uq_quality_metric", "data_quality_metrics", type_="unique")
    op.drop_index("ix_data_quality_metrics_project_id", table_name="data_quality_metrics")
    op.drop_index("ix_data_quality_metrics_dataset_id", table_name="data_quality_metrics")
    op.drop_index("ix_data_quality_metrics_snapshot_id", table_name="data_quality_metrics")
    op.drop_table("data_quality_metrics")
    postgresql.ENUM(name="data_quality_metric_status").drop(op.get_bind())
    postgresql.ENUM(name="data_quality_indicator").drop(op.get_bind())
    postgresql.ENUM(name="data_quality_layer").drop(op.get_bind())
