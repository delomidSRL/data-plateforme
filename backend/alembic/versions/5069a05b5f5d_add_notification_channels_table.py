"""add notification channels table

Revision ID: 5069a05b5f5d
Revises: 1121819ac601
Create Date: 2026-07-15 00:22:38.676119

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '5069a05b5f5d'
down_revision: Union[str, None] = '1121819ac601'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    channel_type = postgresql.ENUM("email", "webhook", name="notification_channel_type")
    alert_severity = postgresql.ENUM("info", "warning", "critical", name="data_quality_alert_severity", create_type=False)

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("type", channel_type, nullable=False),
        sa.Column("config_encrypted", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("min_severity", alert_severity, nullable=False, server_default="warning"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notification_channels_project_id", "notification_channels", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_notification_channels_project_id", table_name="notification_channels")
    op.drop_table("notification_channels")
    postgresql.ENUM(name="notification_channel_type").drop(op.get_bind())
