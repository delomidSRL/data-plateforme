"""add dbt test materialization columns to data_quality_checks

Module 16 extension §5/§7 — opt-in per-check materialization as a dbt test (second,
preventive execution backend). Safe defaults (materialize_as_dbt_test=false,
dbt_test_severity=warn) ⇒ every pre-existing check stays 100% observation-only, zero
regression.

Revision ID: 9ad68133a7a7
Revises: a29d6e0157f4
Create Date: 2026-09-13
"""
from alembic import op
import sqlalchemy as sa

revision = "9ad68133a7a7"
down_revision = "a29d6e0157f4"
branch_labels = None
depends_on = None

_SEVERITY_ENUM = sa.Enum("warn", "error", name="data_quality_dbt_test_severity")


def upgrade() -> None:
    _SEVERITY_ENUM.create(op.get_bind())
    op.add_column(
        "data_quality_checks",
        sa.Column("materialize_as_dbt_test", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "data_quality_checks",
        sa.Column("dbt_test_severity", _SEVERITY_ENUM, nullable=False, server_default="warn"),
    )


def downgrade() -> None:
    op.drop_column("data_quality_checks", "dbt_test_severity")
    op.drop_column("data_quality_checks", "materialize_as_dbt_test")
    _SEVERITY_ENUM.drop(op.get_bind())
