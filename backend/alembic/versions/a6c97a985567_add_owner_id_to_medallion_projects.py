"""add owner_id to medallion projects

Revision ID: a6c97a985567
Revises: d2baa50052db
Create Date: 2026-08-04 18:59:05.805541

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6c97a985567'
down_revision: Union[str, None] = 'd2baa50052db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. nullable column first — existing rows have no owner yet.
    op.add_column("medallion_projects", sa.Column("owner_id", sa.Integer(), nullable=True))

    conn = op.get_bind()

    # 2. backfill, in priority order: the earliest MedallionVersion's author for that
    # project (Module 8 — the person who actually built/deployed it first), else the
    # oldest admin account (bootstrap admin). Never leaves owner_id null.
    #
    # On a from-scratch install, migrations run *before* the admin bootstrap (seed.py,
    # see docker/entrypoint.sh) — medallion_projects is empty at that point, so there is
    # nothing to backfill and no admin lookup is needed at all. Only require one when a
    # row genuinely needs it, otherwise a fresh install fails here for no reason.
    needs_backfill = conn.execute(sa.text("SELECT 1 FROM medallion_projects WHERE owner_id IS NULL LIMIT 1")).scalar()
    if needs_backfill:
        oldest_admin_id = conn.execute(sa.text(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1"
        )).scalar()
        if oldest_admin_id is None:
            raise RuntimeError("Aucun compte admin trouvé — impossible de backfiller medallion_projects.owner_id.")

        conn.execute(
            sa.text(
                """
                UPDATE medallion_projects p
                SET owner_id = COALESCE(
                    (
                        SELECT v.created_by_id
                        FROM medallion_versions v
                        WHERE v.project_id = p.id AND v.created_by_id IS NOT NULL
                        ORDER BY v.version_number ASC
                        LIMIT 1
                    ),
                    :admin_id
                )
                WHERE p.owner_id IS NULL
                """
            ),
            {"admin_id": oldest_admin_id},
        )

    # 3. now safe to enforce NOT NULL + index + FK.
    op.alter_column("medallion_projects", "owner_id", nullable=False)
    op.create_index("ix_medallion_projects_owner_id", "medallion_projects", ["owner_id"])
    op.create_foreign_key("fk_medallion_projects_owner_id_users", "medallion_projects", "users", ["owner_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_medallion_projects_owner_id_users", "medallion_projects", type_="foreignkey")
    op.drop_index("ix_medallion_projects_owner_id", table_name="medallion_projects")
    op.drop_column("medallion_projects", "owner_id")
