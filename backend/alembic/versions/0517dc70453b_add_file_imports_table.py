"""add file_imports table

Revision ID: 0517dc70453b
Revises: 5069a05b5f5d
Create Date: 2026-07-18 14:40:37.254191

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0517dc70453b'
down_revision: Union[str, None] = '5069a05b5f5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    file_import_format = postgresql.ENUM("csv", "excel", "json", "xml", name="file_import_format")
    file_import_write_mode = postgresql.ENUM("create", "replace", "append", name="file_import_write_mode")
    file_import_status = postgresql.ENUM("draft", "awaiting_validation", "importing", "imported", "error", name="file_import_status")

    op.create_table(
        "file_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("imported_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),

        sa.Column("source_file_name", sa.String(500), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("archive_source_id", sa.Integer(), sa.ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True),
        sa.Column("archive_path", sa.String(1000), nullable=True),

        sa.Column("format", file_import_format, nullable=False),
        sa.Column("format_options", postgresql.JSONB(), nullable=False, server_default="{}"),

        sa.Column("column_mapping", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("contract_hash", sa.String(64), nullable=True),

        sa.Column("target_source_id", sa.Integer(), sa.ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_schema", sa.String(120), nullable=False, server_default="imports"),
        sa.Column("target_table", sa.String(120), nullable=True),
        sa.Column("write_mode", file_import_write_mode, nullable=False, server_default="create"),

        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("cast_errors", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("status", file_import_status, nullable=False, server_default="draft"),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_file_imports_target", "file_imports", ["target_source_id", "target_schema", "target_table"])


def downgrade() -> None:
    op.drop_index("ix_file_imports_target", table_name="file_imports")
    op.drop_table("file_imports")
    postgresql.ENUM(name="file_import_status").drop(op.get_bind())
    postgresql.ENUM(name="file_import_write_mode").drop(op.get_bind())
    postgresql.ENUM(name="file_import_format").drop(op.get_bind())
