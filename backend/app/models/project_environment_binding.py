"""Module 17 — promotion dev -> prod: environnements & bindings.

A MedallionProject's *logic* (datasets, lineage, SQL, tests — MedallionDataset/MedallionVersion)
is environment-agnostic. Only the *liaison* to real infrastructure varies: which Airflow
instance, which warehouse, which object store, which schedule/dbt target, and — once deployed —
which dag_id/status/active_version. Those fields, formerly columns on MedallionProject itself,
now live here, one row per (project, environment). A project always has exactly one binding
(`is_home=True`, its dev/working environment) and may additionally have a `prod` binding once
promoted at least once (Module 17 §5).

Note on the spec text vs. this codebase: §3.3 names the moved Airflow field `airflow_stack_id`
(FK infra_stacks) — this platform already generalized that a project's Airflow link is an
`airflow_instance_id` (FK airflow_instances), which itself resolves to either a platform stack
or an external deploy target (services/airflow_instances.py). Reusing that existing, richer
abstraction instead of reintroducing a raw stack FK is the "réutilise l'existant" call here.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.medallion import ProjectStatus, ProjectTarget
from app.models.server import Environment

# ProjectStatus/ProjectTarget are reused as-is (not redefined here) — dozens of call sites
# already compare `project.status`/`.target` against these exact enum members (e.g. `in
# (ProjectStatus.deployed, ProjectStatus.paused)`); a second enum class with the same string
# values happens to still compare equal (str mixin), but there is no reason to mint a second
# Postgres enum type and a second Python class for the same closed vocabulary a binding now
# carries instead of the project — one definition, reused, per §0 "réutilise l'existant".


class ProjectEnvironmentBinding(Base):
    __tablename__ = "project_environment_bindings"
    __table_args__ = (UniqueConstraint("project_id", "environment", name="uq_binding_project_environment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("medallion_projects.id", ondelete="CASCADE"), nullable=False)
    environment: Mapped[Environment] = mapped_column(Enum(Environment, name="binding_environment"), nullable=False)
    # The binding created together with the project — its dev/working environment. Never
    # more than one is_home per project (enforced in service code, not a DB constraint: a
    # partial-unique-index-per-project on a boolean isn't portably expressible here, and this
    # table is only ever written through services/environment_binding.py).
    is_home: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # --- moved from MedallionProject (Module 17 §3.3) ---
    # NOT NULL, matching the original MedallionProject columns' own strictness: a binding
    # (home or prod) is always created with its full infra choice in one submission — the
    # prod "configuration wizard" (§5.3) collects stack/warehouse/object store together,
    # never leaves them unset. draft/built/deployed/... (status, below) is the "not live yet"
    # signal, not "not configured yet".
    airflow_instance_id: Mapped[int] = mapped_column(ForeignKey("airflow_instances.id"), nullable=False)
    object_store_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    warehouse_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    schedule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Same Postgres enum types the columns used before the move (medallion_project_target/
    # _status) — dropping a column doesn't drop its enum type, so these are reused in place,
    # never recreated (SQLAlchemy's create_table() checks-first before CREATE TYPE).
    target: Mapped[ProjectTarget] = mapped_column(Enum(ProjectTarget, name="medallion_project_target"), default=ProjectTarget.dev, nullable=False)
    dag_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dag_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus, name="medallion_project_status"), default=ProjectStatus.draft, nullable=False)
    # M8, moved: each environment tracks its own deployed version independently.
    active_version_id: Mapped[int | None] = mapped_column(ForeignKey("medallion_versions.id"), nullable=True)
