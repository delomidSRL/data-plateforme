"""add data quality rules and alerts tables

Revision ID: 1121819ac601
Revises: d8ef09dedf05
Create Date: 2026-07-15 00:08:45.200744

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1121819ac601'
down_revision: Union[str, None] = 'd8ef09dedf05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_quality_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("volume_variation_pct", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("min_rows", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("freshness_max_hours", sa.Integer(), nullable=False, server_default="26"),
        sa.Column("alert_on_test_failure", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("alert_on_schema_change", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    alert_type = postgresql.ENUM("volume", "freshness", "tests", "schema", name="data_quality_alert_type")
    alert_severity = postgresql.ENUM("info", "warning", "critical", name="data_quality_alert_severity")
    alert_status = postgresql.ENUM("open", "acknowledged", name="data_quality_alert_status")

    op.create_table(
        "data_quality_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("data_quality_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", alert_type, nullable=False),
        sa.Column("severity", alert_severity, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", alert_status, nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_data_quality_alerts_project_id", "data_quality_alerts", ["project_id"])
    op.create_index("ix_data_quality_alerts_dataset_id", "data_quality_alerts", ["dataset_id"])
    op.create_index("ix_data_quality_alerts_status", "data_quality_alerts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_data_quality_alerts_status", table_name="data_quality_alerts")
    op.drop_index("ix_data_quality_alerts_dataset_id", table_name="data_quality_alerts")
    op.drop_index("ix_data_quality_alerts_project_id", table_name="data_quality_alerts")
    op.drop_table("data_quality_alerts")
    postgresql.ENUM(name="data_quality_alert_status").drop(op.get_bind())
    postgresql.ENUM(name="data_quality_alert_severity").drop(op.get_bind())
    postgresql.ENUM(name="data_quality_alert_type").drop(op.get_bind())
    op.drop_table("data_quality_rules")
