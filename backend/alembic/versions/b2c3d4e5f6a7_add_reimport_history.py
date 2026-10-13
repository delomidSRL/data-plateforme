"""add source_location to file_imports + file_import_reimport_events history table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("file_imports", sa.Column("source_location", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # create_type=False — op.create_table() below already issues CREATE TYPE itself for each
    # enum column it creates; calling .create() here too double-creates it (DuplicateObject).
    reimport_trigger_source = postgresql.ENUM("initial", "manual_local", "manual_s3", name="reimport_trigger_source", create_type=False)
    reimport_trigger_source.create(op.get_bind(), checkfirst=True)
    reimport_outcome = postgresql.ENUM("imported", "drift_rejected", "error", name="reimport_outcome", create_type=False)
    reimport_outcome.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "file_import_reimport_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_import_id", sa.Integer(), sa.ForeignKey("file_imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("trigger_source", reimport_trigger_source, nullable=False),
        sa.Column("source_location", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("outcome", reimport_outcome, nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("cast_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_file_import_reimport_events_file_import_id", "file_import_reimport_events", ["file_import_id"])


def downgrade() -> None:
    op.drop_index("ix_file_import_reimport_events_file_import_id", table_name="file_import_reimport_events")
    op.drop_table("file_import_reimport_events")
    op.execute("DROP TYPE reimport_outcome")
    op.execute("DROP TYPE reimport_trigger_source")
    op.drop_column("file_imports", "source_location")
