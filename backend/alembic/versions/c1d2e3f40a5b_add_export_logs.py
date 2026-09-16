"""add export logs (module 11 extension)

Revision ID: c1d2e3f40a5b
Revises: 1fa466570c38
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f40a5b'
down_revision: Union[str, None] = '1fa466570c38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The enum type is created by create_table() below — do NOT call kind.create()
    # explicitly (double-create bug, see file_watch migration).
    kind = postgresql.ENUM("csv", "dbt_project", name="export_log_kind")

    op.create_table(
        "export_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("medallion_datasets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("exported_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("client_ip", sa.String(64), nullable=True),
    )
    op.create_index("ix_export_logs_project_id", "export_logs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_export_logs_project_id", table_name="export_logs")
    op.drop_table("export_logs")
    postgresql.ENUM(name="export_log_kind").drop(op.get_bind())
