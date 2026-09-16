"""add payload import mode and structuration contract (module 6 extension)

Revision ID: d47b1a92e6c1
Revises: c1d2e3f40a5b
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd47b1a92e6c1'
down_revision: Union[str, None] = 'c1d2e3f40a5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import_mode = postgresql.ENUM("typed", "payload", name="file_import_mode")
    import_mode.create(op.get_bind())
    op.add_column(
        "file_imports",
        sa.Column("import_mode", import_mode, nullable=False, server_default="typed"),
    )
    op.alter_column("file_imports", "import_mode", server_default=None)

    quarantine_policy = postgresql.ENUM("report", "block", name="structuration_quarantine_policy")

    op.create_table(
        "payload_structurations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("medallion_datasets.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload_column", sa.String(120), nullable=False, server_default="payload"),
        sa.Column("column_mapping", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("quarantine_policy", quarantine_policy, nullable=False, server_default="report"),
        sa.Column("quarantine_threshold_pct", sa.Float(), nullable=True),
        sa.Column("contract_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("payload_structurations")
    postgresql.ENUM(name="structuration_quarantine_policy").drop(op.get_bind())
    op.drop_column("file_imports", "import_mode")
    postgresql.ENUM(name="file_import_mode").drop(op.get_bind())
