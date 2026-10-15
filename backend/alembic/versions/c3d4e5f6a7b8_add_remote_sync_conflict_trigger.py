"""add remote_sync conflict trigger (code tab <-> physical folder sync)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres forbids using a freshly-added enum value in the same transaction that added it —
    # autocommit_block() commits the ALTER TYPE on its own (same pattern as 6acc7e062bdf).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE project_file_conflict_trigger ADD VALUE IF NOT EXISTS 'remote_sync'")


def downgrade() -> None:
    pass
    # Postgres enum values can't be dropped without rebuilding the type — left in place (same
    # documented limitation as 6acc7e062bdf's own downgrade).
