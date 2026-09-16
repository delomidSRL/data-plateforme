"""add file watches (module 6 extension, etape 1)

Revision ID: 1fa466570c38
Revises: f96809b96780
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1fa466570c38'
down_revision: Union[str, None] = 'f96809b96780'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    transport = postgresql.ENUM("minio", "local", name="file_watch_transport")
    pattern_type = postgresql.ENUM("glob", "regex", name="file_watch_pattern_type")
    completeness = postgresql.ENUM("stable_size", "control_file", "none", name="file_watch_completeness_strategy")
    post_process = postgresql.ENUM("record_only", "move", name="file_watch_post_process")
    write_mode = postgresql.ENUM("append", "replace", name="file_watch_write_mode")
    watch_status = postgresql.ENUM("active", "paused", "error", name="file_watch_status")
    outcome = postgresql.ENUM("imported", "skipped_duplicate", "drift_rejected", "fetch_error", "absent_sla", name="file_watch_outcome")
    severity = postgresql.ENUM("info", "warning", "critical", name="file_watch_severity")

    op.create_table(
        "file_watches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("file_import_id", sa.Integer(), sa.ForeignKey("file_imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transport", transport, nullable=False),
        sa.Column("location", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("pattern", sa.String(500), nullable=False),
        sa.Column("pattern_type", pattern_type, nullable=False, server_default="glob"),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("completeness_strategy", completeness, nullable=False, server_default="stable_size"),
        sa.Column("stable_size_delay_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("control_file_suffix", sa.String(50), nullable=False, server_default=".done"),
        sa.Column("arrival_cron", sa.String(120), nullable=True),
        sa.Column("arrival_grace_minutes", sa.Integer(), nullable=True),
        sa.Column("post_process", post_process, nullable=False, server_default="record_only"),
        sa.Column("done_target", sa.String(1000), nullable=True),
        sa.Column("error_target", sa.String(1000), nullable=True),
        sa.Column("write_mode", write_mode, nullable=False),
        sa.Column("status", watch_status, nullable=False, server_default="active"),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_file", sa.String(500), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_file_watches_status", "file_watches", ["status"])

    op.create_table(
        "file_watch_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("watch_id", sa.Integer(), sa.ForeignKey("file_watches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_checksum", sa.String(64), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("outcome", outcome, nullable=False),
        sa.Column("rows_imported", sa.BigInteger(), nullable=True),
        sa.Column("cast_errors", postgresql.JSONB(), nullable=True),
        sa.Column("file_import_reimport_ref", sa.String(64), nullable=True),
        sa.Column("severity", severity, nullable=False, server_default="info"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_file_watch_events_watch_id", "file_watch_events", ["watch_id"])
    op.create_unique_constraint("uq_file_watch_event_checksum", "file_watch_events", ["watch_id", "file_checksum"])


def downgrade() -> None:
    op.drop_constraint("uq_file_watch_event_checksum", "file_watch_events", type_="unique")
    op.drop_index("ix_file_watch_events_watch_id", table_name="file_watch_events")
    op.drop_table("file_watch_events")
    op.drop_index("ix_file_watches_status", table_name="file_watches")
    op.drop_table("file_watches")
    postgresql.ENUM(name="file_watch_severity").drop(op.get_bind())
    postgresql.ENUM(name="file_watch_outcome").drop(op.get_bind())
    postgresql.ENUM(name="file_watch_status").drop(op.get_bind())
    postgresql.ENUM(name="file_watch_write_mode").drop(op.get_bind())
    postgresql.ENUM(name="file_watch_post_process").drop(op.get_bind())
    postgresql.ENUM(name="file_watch_completeness_strategy").drop(op.get_bind())
    postgresql.ENUM(name="file_watch_pattern_type").drop(op.get_bind())
    postgresql.ENUM(name="file_watch_transport").drop(op.get_bind())
