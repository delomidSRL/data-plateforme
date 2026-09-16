"""add airflow_instances table, migrate medallion_projects to airflow_instance_id

Revision ID: ed9eb95bc0ef
Revises: 0517dc70453b
Create Date: 2026-07-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ed9eb95bc0ef'
down_revision: Union[str, None] = '0517dc70453b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('airflow_instances',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('origin', sa.Enum('platform', 'external', name='airflow_instance_origin'), nullable=False),
    sa.Column('stack_id', sa.Integer(), nullable=True),
    sa.Column('base_url', sa.String(length=500), nullable=False),
    sa.Column('api_version', sa.String(length=10), nullable=False),
    sa.Column('airflow_version', sa.String(length=30), nullable=True),
    sa.Column('username', sa.String(length=255), nullable=False),
    sa.Column('secret_encrypted', sa.String(), nullable=False),
    sa.Column('verify_tls', sa.Boolean(), nullable=False),
    sa.Column('deploy_access', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('capabilities', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('unknown', 'reachable', 'unreachable', 'unsupported_version', name='airflow_instance_status'), nullable=False),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['stack_id'], ['infra_stacks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )

    # --- derive-value migration: one platform AirflowInstance per InfraStack with Airflow enabled ---
    conn = op.get_bind()
    stacks = conn.execute(sa.text(
        "SELECT ist.id AS stack_id, ist.name AS stack_name, ist.services AS services, srv.hostname AS hostname "
        "FROM infra_stacks ist JOIN servers srv ON srv.id = ist.server_id"
    )).mappings().all()

    airflow_instances = sa.table(
        'airflow_instances',
        sa.column('id', sa.Integer),
        sa.column('name', sa.String),
        sa.column('origin', postgresql.ENUM('platform', 'external', name='airflow_instance_origin', create_type=False)),
        sa.column('stack_id', sa.Integer),
        sa.column('base_url', sa.String),
        sa.column('api_version', sa.String),
        sa.column('username', sa.String),
        sa.column('secret_encrypted', sa.String),
        sa.column('verify_tls', sa.Boolean),
        sa.column('capabilities', postgresql.JSONB),
        sa.column('status', postgresql.ENUM('unknown', 'reachable', 'unreachable', 'unsupported_version', name='airflow_instance_status', create_type=False)),
        sa.column('is_active', sa.Boolean),
    )

    stack_to_instance_id: dict[int, int] = {}
    for row in stacks:
        airflow_conf = (row['services'] or {}).get('airflow') or {}
        if not airflow_conf.get('enabled'):
            continue
        result = conn.execute(
            airflow_instances.insert().values(
                name=f"{row['stack_name']} (Airflow)",
                origin='platform',
                stack_id=row['stack_id'],
                base_url=f"http://{row['hostname']}:{airflow_conf.get('web_port', 8080)}",
                api_version='v2',
                username=airflow_conf.get('admin_user', 'airflow'),
                secret_encrypted=airflow_conf.get('admin_password', ''),
                verify_tls=True,
                capabilities={"deployable": True},
                status='unknown',
                is_active=True,
            ).returning(airflow_instances.c.id)
        )
        stack_to_instance_id[row['stack_id']] = result.scalar()

    # --- add the new FK on medallion_projects (nullable first, to allow backfill) ---
    op.add_column('medallion_projects', sa.Column('airflow_instance_id', sa.Integer(), nullable=True))
    op.create_foreign_key('medallion_projects_airflow_instance_id_fkey', 'medallion_projects', 'airflow_instances', ['airflow_instance_id'], ['id'])

    for stack_id, instance_id in stack_to_instance_id.items():
        conn.execute(sa.text(
            "UPDATE medallion_projects SET airflow_instance_id = :iid WHERE airflow_stack_id = :sid"
        ), {"iid": instance_id, "sid": stack_id})

    # A project pointing at a stack whose Airflow was never enabled would be left with a NULL
    # FK here, violating the NOT NULL below — fail loudly rather than silently corrupt a real
    # project's target.
    orphans = conn.execute(sa.text(
        "SELECT id, name FROM medallion_projects WHERE airflow_instance_id IS NULL"
    )).fetchall()
    if orphans:
        raise RuntimeError(
            f"Migration impossible : {len(orphans)} projet(s) medallion referencent une stack "
            f"sans Airflow active : {[r[1] for r in orphans]}. A corriger manuellement avant de continuer."
        )

    op.alter_column('medallion_projects', 'airflow_instance_id', nullable=False)
    op.drop_constraint('medallion_projects_airflow_stack_id_fkey', 'medallion_projects', type_='foreignkey')
    op.drop_column('medallion_projects', 'airflow_stack_id')


def downgrade() -> None:
    op.add_column('medallion_projects', sa.Column('airflow_stack_id', sa.Integer(), nullable=True))
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE medallion_projects mp SET airflow_stack_id = ai.stack_id "
        "FROM airflow_instances ai WHERE ai.id = mp.airflow_instance_id"
    ))
    op.alter_column('medallion_projects', 'airflow_stack_id', nullable=False)
    op.create_foreign_key('medallion_projects_airflow_stack_id_fkey', 'medallion_projects', 'infra_stacks', ['airflow_stack_id'], ['id'])
    op.drop_constraint('medallion_projects_airflow_instance_id_fkey', 'medallion_projects', type_='foreignkey')
    op.drop_column('medallion_projects', 'airflow_instance_id')
    op.drop_table('airflow_instances')
    op.execute("DROP TYPE IF EXISTS airflow_instance_origin")
    op.execute("DROP TYPE IF EXISTS airflow_instance_status")
