import posixpath

import yaml
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.security import decrypt_secret
from app.models.data_source import DataSource
from app.models.medallion import (
    LoadMode,
    Materialization,
    MedallionDataset,
    MedallionLayer,
    MedallionProject,
    MedallionVersion,
    MLObjective,
    ProjectStatus,
    TransformType,
)
from app.models.user import User
from app.services import dag_render, dbt_project, ssh, version_snapshot
from app.services.airflow_instances import DeployTarget
from app.services.medallion_deploy import _deploy_dag_via_ssh, _deploy_files_via_ssh, _resolve_endpoint, _upsert_connection

_DATASET_FIELDS = (
    "source_id", "source_object", "load_mode", "incremental_key", "partition_by", "bronze_location",
    "dbt_model_name", "materialization", "sql", "transform_type", "ml_objective", "python_code",
    "output_table", "tests", "description",
)


def _reconcile_datasets(db: Session, project: MedallionProject, datasets_snapshot: list[dict]) -> list[MedallionDataset]:
    """Aligns the live MedallionDataset rows onto datasets_snapshot, matched by `name`
    (the stable key, §3.3) — present on both sides is updated in place (id kept, so
    lineage and any downstream reference stay intact); present live-only is deleted;
    present snapshot-only is recreated. Lineage is then re-resolved by name, since ids
    may differ from when the snapshot was taken."""
    live = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    live_by_name = {d.name: d for d in live}
    snapshot_by_name = {d["name"]: d for d in datasets_snapshot}

    for name, d in list(live_by_name.items()):
        if name not in snapshot_by_name:
            db.delete(d)
            del live_by_name[name]
    db.flush()

    for name, snap in snapshot_by_name.items():
        fields = dict(
            layer=MedallionLayer(snap["layer"]),
            name=snap["name"],
            source_id=snap["source_id"],
            source_object=snap["source_object"],
            load_mode=LoadMode(snap["load_mode"]) if snap["load_mode"] else None,
            incremental_key=snap["incremental_key"],
            partition_by=snap["partition_by"],
            bronze_location=snap["bronze_location"],
            dbt_model_name=snap["dbt_model_name"],
            materialization=Materialization(snap["materialization"]) if snap["materialization"] else None,
            sql=snap["sql"],
            transform_type=TransformType(snap["transform_type"]),
            ml_objective=MLObjective(snap["ml_objective"]),
            python_code=snap["python_code"],
            output_table=snap["output_table"],
            tests=snap["tests"] or [],
            description=snap["description"],
        )
        existing = live_by_name.get(name)
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
        else:
            new_ds = MedallionDataset(project_id=project.id, upstream_dataset_ids=[], **fields)
            db.add(new_ds)
            db.flush()
            live_by_name[name] = new_ds

    # re-resolve lineage by name now that every row exists with its (possibly new) id
    for name, snap in snapshot_by_name.items():
        ds = live_by_name[name]
        upstream_names = snap.get("upstream_dataset_names") or []
        ds.upstream_dataset_ids = [live_by_name[n].id for n in upstream_names if n in live_by_name]

    db.flush()
    return list(live_by_name.values())


async def restore(
    db: Session,
    project: MedallionProject,
    target_version: MedallionVersion,
    warehouse: DataSource,
    object_store: DataSource,
    target: DeployTarget,
    user: User | None,
) -> MedallionVersion:
    """Redeploys `target_version`'s captured artifacts verbatim (never regenerated), then
    reconciles the live dataset definitions onto its datasets_snapshot, and records the
    restore itself as a new version. Never drops a warehouse table, never triggers a run —
    the data is rebuilt on the next run the engineer launches when ready (§4.3)."""
    base_url, admin_user, admin_password = target.airflow.base_url, target.airflow.username, target.airflow.password

    # 1. faithful redeploy — snapshot files verbatim, except profiles.yml, whose
    # credentials are never stored and are re-injected fresh from the project's *current*
    # data_sources (never from the snapshot, never from an old/possibly-rotated secret).
    wh_host, wh_port = _resolve_endpoint(warehouse, target)
    warehouse_dict = {
        "host": wh_host, "port": wh_port, "username": warehouse.username,
        "password": decrypt_secret(warehouse.secret_encrypted), "database_name": warehouse.database_name,
    }
    dbt_files = dict(target_version.dbt_project_snapshot)
    dbt_files["profiles.yml"] = yaml.safe_dump(dbt_project._profiles_yml(project, warehouse_dict), sort_keys=False)

    await run_in_threadpool(_deploy_files_via_ssh, target, target.dbt_dir, target.plugins_dir, dbt_files)

    async def _upsert(source: DataSource, conn_id: str):
        await _upsert_connection(base_url, admin_user, admin_password, source, target, conn_id)

    # Module 17 — restore redeploys a captured dag_snapshot VERBATIM (never re-rendered), so
    # its embedded warehouse/object-store conn_id could be either scheme depending on when
    # that version was captured: the pre-Module-17 id-based one (conn_id_for_source) or the
    # current project-role one (conn_id_for_project_role, stable across environments). Rather
    # than parse the snapshot text to tell which, register the current credentials under BOTH
    # — a small registration redundancy that guarantees a historical restore keeps working
    # either way (§0 "zéro régression" on real, already-deployed projects).
    await _upsert(warehouse, dag_render.conn_id_for_source(project.warehouse_source_id))
    await _upsert(object_store, dag_render.conn_id_for_source(project.object_store_source_id))
    await _upsert(warehouse, dag_render.conn_id_for_project_role(project.id, "warehouse"))
    await _upsert(object_store, dag_render.conn_id_for_project_role(project.id, "object_store"))

    bronze_source_ids = {
        d["source_id"] for d in target_version.datasets_snapshot
        if d["layer"] == "bronze" and d.get("source_id")
    }
    bronze_sources = db.query(DataSource).filter(DataSource.id.in_(bronze_source_ids)).all() if bronze_source_ids else []
    for source in bronze_sources:
        await _upsert(source, dag_render.conn_id_for_source(source.id))

    # Module 17 — restore stays scoped to the HOME binding (disclosed scope: a general
    # restore-any-binding isn't part of this module's promotion story). `dag_snapshot` is
    # always captured in BASE, unsuffixed form (§2); the environment suffix is applied here,
    # the same as a normal build, never baked into what MedallionVersion stores.
    home_binding = project.home_binding
    environment = home_binding.environment.value
    dag_id_base = dag_render.dag_id_for_project(project)
    dag_id = dag_render.dag_id_for_environment(project, environment)
    dag_content = dag_render.apply_environment_suffix(target_version.dag_snapshot, project, environment)
    dag_path = posixpath.join(target.dags_dir, f"{dag_id}.py")

    if home_binding.dag_file_path and home_binding.dag_file_path != dag_path:
        def _delete_stale_dag():
            try:
                with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
                    ssh.run_command(client, f"rm -f {home_binding.dag_file_path}")
            except Exception:
                pass
        await run_in_threadpool(_delete_stale_dag)

    await run_in_threadpool(_deploy_dag_via_ssh, target, dag_path, dag_content)

    # 2. reconcile the editable screen onto the restored definition (§0) — never touches
    # what was just deposited on Airflow.
    reconciled = _reconcile_datasets(db, project, target_version.datasets_snapshot)

    home_binding.dag_id = dag_id
    home_binding.dag_file_path = dag_path
    home_binding.status = ProjectStatus.deployed
    project.has_pending_changes = False
    db.commit()

    # 3. trace: the restore is itself a new version, never lost — reuses the exact
    # artifacts just redeployed (already credential-free), no second capture logic. Captured
    # in BASE form, like every other capture (§2) — dag_id_base/target_version.dag_snapshot,
    # never the environment-suffixed dag_id/dag_content just deployed above.
    new_version = version_snapshot.capture(
        db, project, home_binding, reconciled, dict(target_version.dbt_project_snapshot), target_version.dag_snapshot,
        dag_id_base, dag_path, user,
    )
    new_version.is_restore_of = target_version.id
    db.commit()
    db.refresh(new_version)
    return new_version
