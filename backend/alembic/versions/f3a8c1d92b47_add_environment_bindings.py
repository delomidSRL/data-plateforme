"""module 17 - server environment tag + project environment bindings (backfill)

Revision ID: f3a8c1d92b47
Revises: d47b1a92e6c1
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f3a8c1d92b47'
down_revision: Union[str, None] = 'd47b1a92e6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Server.environment — nullable, backfilled, then NOT NULL. Existing server(s) are the
    # dev/working stack this whole platform has been iterating against so far — backfilled
    # 'dev' (decision confirmed with the user, not the spec's generic 'prod' proposal).
    server_environment = postgresql.ENUM("dev", "prod", name="server_environment")
    server_environment.create(bind)
    op.add_column("servers", sa.Column("environment", server_environment, nullable=True))
    bind.execute(sa.text("UPDATE servers SET environment = 'dev' WHERE environment IS NULL"))
    op.alter_column("servers", "environment", nullable=False)

    # 2. project_environment_bindings — new table. `target`/`status` reuse the EXISTING
    # medallion_project_target/_status enum types verbatim (create_type=False): dropping a
    # column never drops its type, so those two types already exist from medallion_projects's
    # original definition and must not be recreated here.
    op.create_table(
        "project_environment_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment", postgresql.ENUM("dev", "prod", name="binding_environment"), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("airflow_instance_id", sa.Integer(), sa.ForeignKey("airflow_instances.id"), nullable=False),
        sa.Column("object_store_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("warehouse_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("schedule", sa.String(120), nullable=True),
        sa.Column("target", postgresql.ENUM("dev", "prod", name="medallion_project_target", create_type=False), nullable=False, server_default="dev"),
        sa.Column("dag_id", sa.String(200), nullable=True),
        sa.Column("dag_file_path", sa.String(500), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("draft", "built", "deployed", "paused", "error", name="medallion_project_status", create_type=False),
            nullable=False, server_default="draft",
        ),
        sa.Column("active_version_id", sa.Integer(), sa.ForeignKey("medallion_versions.id"), nullable=True),
        sa.UniqueConstraint("project_id", "environment", name="uq_binding_project_environment"),
    )

    # 3. Backfill — one is_home=true binding per existing project, copying its current infra
    # fields verbatim. Environment resolved via the project's Airflow instance -> (if platform)
    # stack -> server -> environment; an external instance (no server) defaults to 'dev', same
    # reasoning as the server backfill itself.
    bind.execute(sa.text("""
        INSERT INTO project_environment_bindings
            (project_id, environment, is_home, airflow_instance_id, object_store_source_id,
             warehouse_source_id, schedule, target, dag_id, dag_file_path, status, active_version_id)
        SELECT
            p.id, COALESCE(s.environment::text, 'dev')::binding_environment, true,
            p.airflow_instance_id, p.object_store_source_id, p.warehouse_source_id,
            p.schedule, p.target, p.dag_id, p.dag_file_path, p.status, p.active_version_id
        FROM medallion_projects p
        LEFT JOIN airflow_instances ai ON ai.id = p.airflow_instance_id
        LEFT JOIN infra_stacks st ON st.id = ai.stack_id
        LEFT JOIN servers s ON s.id = st.server_id
    """))

    # 4. Drop the now-migrated columns from medallion_projects — the moved fields live only
    # on the binding from here on (medallion_project_target/_status stay, reused above).
    op.drop_column("medallion_projects", "object_store_source_id")
    op.drop_column("medallion_projects", "warehouse_source_id")
    op.drop_column("medallion_projects", "airflow_instance_id")
    op.drop_column("medallion_projects", "target")
    op.drop_column("medallion_projects", "schedule")
    op.drop_column("medallion_projects", "dag_id")
    op.drop_column("medallion_projects", "dag_file_path")
    op.drop_column("medallion_projects", "status")
    op.drop_column("medallion_projects", "active_version_id")


def downgrade() -> None:
    bind = op.get_bind()

    op.add_column("medallion_projects", sa.Column("object_store_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=True))
    op.add_column("medallion_projects", sa.Column("warehouse_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=True))
    op.add_column("medallion_projects", sa.Column("airflow_instance_id", sa.Integer(), sa.ForeignKey("airflow_instances.id"), nullable=True))
    op.add_column("medallion_projects", sa.Column(
        "target", postgresql.ENUM("dev", "prod", name="medallion_project_target", create_type=False), nullable=False, server_default="dev",
    ))
    op.add_column("medallion_projects", sa.Column("schedule", sa.String(120), nullable=True))
    op.add_column("medallion_projects", sa.Column("dag_id", sa.String(200), nullable=True))
    op.add_column("medallion_projects", sa.Column("dag_file_path", sa.String(500), nullable=True))
    op.add_column("medallion_projects", sa.Column(
        "status",
        postgresql.ENUM("draft", "built", "deployed", "paused", "error", name="medallion_project_status", create_type=False),
        nullable=False, server_default="draft",
    ))
    op.add_column("medallion_projects", sa.Column("active_version_id", sa.Integer(), sa.ForeignKey("medallion_versions.id"), nullable=True))

    # Restore from each project's home binding (best-effort: a project created/promoted after
    # the upgrade with more than the original single binding loses its non-home bindings here —
    # downgrade is a last-resort escape hatch, not a lossless round-trip).
    bind.execute(sa.text("""
        UPDATE medallion_projects p SET
            object_store_source_id = b.object_store_source_id,
            warehouse_source_id = b.warehouse_source_id,
            airflow_instance_id = b.airflow_instance_id,
            target = b.target,
            schedule = b.schedule,
            dag_id = b.dag_id,
            dag_file_path = b.dag_file_path,
            status = b.status,
            active_version_id = b.active_version_id
        FROM project_environment_bindings b
        WHERE b.project_id = p.id AND b.is_home = true
    """))

    op.alter_column("medallion_projects", "object_store_source_id", nullable=False)
    op.alter_column("medallion_projects", "warehouse_source_id", nullable=False)
    op.alter_column("medallion_projects", "airflow_instance_id", nullable=False)

    op.drop_table("project_environment_bindings")
    postgresql.ENUM(name="binding_environment").drop(bind)

    op.drop_column("servers", "environment")
    postgresql.ENUM(name="server_environment").drop(bind)
