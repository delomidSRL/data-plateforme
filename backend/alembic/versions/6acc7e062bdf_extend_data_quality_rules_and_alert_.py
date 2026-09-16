"""extend data quality rules and alert types (module 16 etape 5)

Revision ID: 6acc7e062bdf
Revises: b2a6123646a9
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6acc7e062bdf'
down_revision: Union[str, None] = 'b2a6123646a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres forbids using a freshly-added enum value in the same transaction that added it —
    # autocommit_block() commits each ALTER TYPE on its own (same pattern already established in
    # 68efc079b374_add_record_linkage_ml_objective_and_.py).
    with op.get_context().autocommit_block():
        for value in ("integrity", "validity", "consistency", "reconciliation", "plausibility", "completeness"):
            op.execute(f"ALTER TYPE data_quality_alert_type ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column("data_quality_rules", sa.Column("orphan_rate_max", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("data_quality_rules", sa.Column("join_loss_max_pct", sa.Float(), nullable=False, server_default="5.0"))
    op.add_column("data_quality_rules", sa.Column("validity_min_pct", sa.Float(), nullable=False, server_default="95.0"))
    op.add_column("data_quality_rules", sa.Column("reconciliation_tolerance_pct", sa.Float(), nullable=False, server_default="0.5"))
    op.add_column("data_quality_rules", sa.Column("plausibility_max_pct", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("data_quality_rules", sa.Column("layer_weights", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("data_quality_rules", "layer_weights")
    op.drop_column("data_quality_rules", "plausibility_max_pct")
    op.drop_column("data_quality_rules", "reconciliation_tolerance_pct")
    op.drop_column("data_quality_rules", "validity_min_pct")
    op.drop_column("data_quality_rules", "join_loss_max_pct")
    op.drop_column("data_quality_rules", "orphan_rate_max")
    # Postgres enum values can't be dropped without rebuilding the type — left in place (same
    # documented limitation as 68efc079b374's own downgrade).
