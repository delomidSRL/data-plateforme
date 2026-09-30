"""Module 19 étape 2 — édition & synchronisation code -> canvas

Revision ID: 2b3c4d5e6f7a
Revises: 1a2b3c4d5e6f
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2b3c4d5e6f7a'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    dataset_origin = postgresql.ENUM("visual", "code", name="medallion_dataset_origin")
    dataset_origin.create(bind)
    op.add_column("medallion_datasets", sa.Column("origin", dataset_origin, nullable=False, server_default="visual"))

    parse_status = postgresql.ENUM("ok", "error", name="medallion_workspace_parse_status")
    parse_status.create(bind)
    op.add_column("medallion_projects", sa.Column("workspace_parse_status", parse_status, nullable=False, server_default="ok"))
    op.add_column("medallion_projects", sa.Column("workspace_parse_errors", postgresql.JSONB(), nullable=True))

    # Unlike the two enums above, this one is used inline in a create_table below — Alembic's
    # own table emission already creates its column types (checkfirst=False internally), so
    # a separate explicit .create(bind) here would double-CREATE TYPE and fail.
    audit_action = postgresql.ENUM(
        "create", "update", "move", "delete", "accept_merge", "resolve", "discard",
        name="project_file_audit_action",
    )
    op.create_table(
        "project_file_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("generator", sa.String(32), nullable=True),
        sa.Column("content_hash_before", sa.String(64), nullable=True),
        sa.Column("content_hash_after", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_project_file_audit_project_id", "project_file_audit", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_file_audit_project_id", table_name="project_file_audit")
    op.drop_table("project_file_audit")
    postgresql.ENUM(name="project_file_audit_action").drop(op.get_bind())

    op.drop_column("medallion_projects", "workspace_parse_errors")
    op.drop_column("medallion_projects", "workspace_parse_status")
    postgresql.ENUM(name="medallion_workspace_parse_status").drop(op.get_bind())

    op.drop_column("medallion_datasets", "origin")
    postgresql.ENUM(name="medallion_dataset_origin").drop(op.get_bind())
