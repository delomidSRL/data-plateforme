"""add data quality checks (module 16 etape 4)

Revision ID: b2a6123646a9
Revises: 75c95ba8812b
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2a6123646a9'
down_revision: Union[str, None] = '75c95ba8812b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    check_type = postgresql.ENUM(
        "type_conformity", "format_validity", "intra_row_consistency", "plausibility", "conditional_completeness",
        name="data_quality_check_type",
    )
    check_source = postgresql.ENUM("ai_suggested", "engineer", name="data_quality_check_source")
    check_status = postgresql.ENUM("proposed", "active", "dismissed", name="data_quality_check_status")

    op.create_table(
        "data_quality_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("layer", postgresql.ENUM("bronze", "silver", "gold", name="data_quality_layer", create_type=False), nullable=False),
        sa.Column("target_column", sa.String(255), nullable=False, server_default=""),
        sa.Column("check_type", check_type, nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", check_source, nullable=False),
        sa.Column("status", check_status, nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "uq_quality_checks_identity", "data_quality_checks",
        ["check_type", "target_column", sa.text("coalesce(dataset_id, -1)"), sa.text("md5(parameters::text)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_quality_checks_identity", table_name="data_quality_checks")
    op.drop_table("data_quality_checks")
    postgresql.ENUM(name="data_quality_check_status").drop(op.get_bind())
    postgresql.ENUM(name="data_quality_check_source").drop(op.get_bind())
    postgresql.ENUM(name="data_quality_check_type").drop(op.get_bind())
