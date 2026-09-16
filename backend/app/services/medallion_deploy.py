import posixpath

from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.data_source import DataSource, DataSourceOrigin, DataSourceType
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.models.payload_structuration import PayloadStructuration
from app.models.project_environment_binding import ProjectEnvironmentBinding
from app.services import airflow_api, dag_render, dbt_project, ssh
from app.services.airflow_instances import DeployTarget
from app.core.security import decrypt_secret

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), keep_trailing_newline=True)

_CONN_TYPE_MAP = {
    DataSourceType.postgresql: "postgres",
    DataSourceType.mysql: "mysql",
    DataSourceType.oracle: "oracle",
    DataSourceType.minio: "generic",
}


def _resolve_endpoint(source: DataSource, target: DeployTarget) -> tuple[str, int]:
    """Sources co-located on the same docker-compose stack as a *platform* Airflow are
    reached via their internal service name, not the server's external host/port. An
    external Airflow is never co-located with anything the platform provisioned."""
    if target.is_platform and source.origin == DataSourceOrigin.platform and source.stack_id == target.stack_id:
        if source.type == DataSourceType.postgresql:
            return "postgres-standalone", 5432
        if source.type == DataSourceType.minio:
            return "minio", 9000
    return source.host, source.port


def _deploy_files_via_ssh(target: DeployTarget, dbt_dir: str, plugins_dir: str, dbt_files: dict[str, str]) -> None:
    ingest_code = _env.get_template("medallion/ingest_bronze.py.j2").render()
    ml_transform_code = _env.get_template("medallion/ml_transform.py.j2").render()

    with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
        # Wipe the previous build's model files before depositing the current set — otherwise
        # a dataset renamed/deleted since the last build leaves an orphaned .sql file on disk
        # that dbt still tries to compile, referencing a source/ref that no longer exists.
        ssh.run_command(client, f"rm -rf {posixpath.join(dbt_dir, 'models')}")
        # Module 16 extension §2/§4 — same orphan problem for materialized-test artifacts:
        # `tests/` and `packages.yml` only exist in `dbt_files` while at least one check is
        # opted in (Tier C / Tier A respectively). A dismissed/un-flagged check must make its
        # file disappear on the NEXT build (§2 réversibilité) — wiped unconditionally here,
        # like `models/`, then only recreated below if still present in `dbt_files`.
        ssh.run_command(client, f"rm -rf {posixpath.join(dbt_dir, 'tests')}")
        ssh.run_command(client, f"rm -f {posixpath.join(dbt_dir, 'packages.yml')}")

        dirs = {posixpath.dirname(posixpath.join(dbt_dir, rel)) for rel in dbt_files}
        dirs.add(dbt_dir)
        for d in dirs:
            ssh.run_command(client, f"mkdir -p {d}")
        ssh.run_command(client, f"mkdir -p {plugins_dir}")

        sftp = client.open_sftp()
        try:
            for rel_path, content in dbt_files.items():
                with sftp.file(posixpath.join(dbt_dir, rel_path), "w") as f:
                    f.write(content)
            with sftp.file(posixpath.join(plugins_dir, "dataplateforme_ingest.py"), "w") as f:
                f.write(ingest_code)
            with sftp.file(posixpath.join(plugins_dir, "dataplateforme_ml_transform.py"), "w") as f:
                f.write(ml_transform_code)
        finally:
            sftp.close()

        # Files land on disk owned by the SSH user, but Airflow's containers run as a
        # fixed uid/gid (airflow:root) that rarely matches it — dbt then can't create its
        # own target/ dir and crashes with no output. Existing projects only worked because
        # a prior full container recreation happened to chown everything once; new projects
        # created afterward never get that. Force "other" write access so it never depends
        # on that timing.
        ssh.run_command(client, f"chmod -R o+rwX {dbt_dir}")
        ssh.run_command(client, f"chmod -R o+rwX {plugins_dir}")


def _deploy_dag_via_ssh(target: DeployTarget, dag_path: str, dag_content: str) -> None:
    ssh.write_remote_file(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret, dag_path, dag_content)


async def _upsert_connection(base_url: str, admin_user: str, admin_password: str, source: DataSource, target: DeployTarget, conn_id: str) -> None:
    """Create-or-refresh a single Airflow connection for `source`. Reused by both
    build_project() and version_restore.py (Module 8) — a restore must ensure the exact
    same connections a normal build would, not a parallel copy of this logic."""
    host, port = _resolve_endpoint(source, target)
    secret = decrypt_secret(source.secret_encrypted)
    extra = None
    if source.type == DataSourceType.minio:
        import json

        extra = json.dumps({"secure": bool((source.options or {}).get("secure", False))})
    conn_kwargs = dict(
        connection_id=conn_id, conn_type=_CONN_TYPE_MAP[source.type],
        host=host, login=source.username, conn_password=secret,
        schema=source.database_name, port=port, extra=extra,
        description=f"Auto-créée par Data Plateforme pour {source.name}",
    )
    try:
        await airflow_api.create_connection(base_url, admin_user, admin_password, **conn_kwargs)
    except airflow_api.AirflowAPIError as exc:
        if exc.status_code != 409:
            raise
        # Already exists — the connections API has no upsert, so re-push the current
        # config via PATCH. Without this, a stale/wrong conn_type or credential from an
        # earlier build would silently persist forever across rebuilds.
        await airflow_api.update_connection(base_url, admin_user, admin_password, **conn_kwargs)


async def build_project(
    db: Session,
    project: MedallionProject,
    binding: ProjectEnvironmentBinding,
    datasets: list[MedallionDataset],
    warehouse: DataSource,
    object_store: DataSource,
    target: DeployTarget,
) -> dict:
    """Module 17 §4.1/§4.2 — generalized to operate on an explicit binding (home/dev, or any
    other environment) rather than reading straight off the project: `binding.schedule`/
    `.environment` drive the dag_id suffix and the deployment's activation decision, never
    `project.schedule` (which is always the HOME binding via the model's read shim — wrong
    for building any other binding)."""
    base_url, admin_user, admin_password = target.airflow.base_url, target.airflow.username, target.airflow.password
    environment = binding.environment.value

    # 1. generate dbt project files
    wh_host, wh_port = _resolve_endpoint(warehouse, target)
    warehouse_dict = {
        "host": wh_host,
        "port": wh_port,
        "username": warehouse.username,
        "password": decrypt_secret(warehouse.secret_encrypted),
        "database_name": warehouse.database_name,
    }
    # Module 6 extension (payload & structuration) — every bronze dataset's validated
    # contract, keyed by dataset id; generate_project_files renders `__parsed`/`__quarantine`
    # for exactly the ones present here (additive, §5.2).
    bronze_ids = [ds.id for ds in datasets if ds.layer == MedallionLayer.bronze]
    structurations = (
        {s.dataset_id: s for s in db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id.in_(bronze_ids)).all()}
        if bronze_ids else {}
    )
    dbt_files = dbt_project.generate_project_files(db, project, datasets, warehouse_dict, structurations)

    await run_in_threadpool(_deploy_files_via_ssh, target, target.dbt_dir, target.plugins_dir, dbt_files)

    # 2. create/refresh Airflow connections. Warehouse/object store use a project-role conn_id
    # (stable across environments, §2) — the credentials pushed are THIS binding's own,
    # exactly what "connexions résolues vers les sources du binding cible" means for a build
    # (as opposed to a promotion, where the frozen DAG's conn_id keys instead come from the
    # binding it was originally captured on — see services/promotion.py).
    connections_created: list[str] = []

    async def _upsert(source: DataSource, conn_id: str):
        await _upsert_connection(base_url, admin_user, admin_password, source, target, conn_id)
        connections_created.append(conn_id)

    await _upsert(warehouse, dag_render.conn_id_for_project_role(project.id, "warehouse"))
    await _upsert(object_store, dag_render.conn_id_for_project_role(project.id, "object_store"))

    bronze_source_ids = {ds.source_id for ds in datasets if ds.layer == MedallionLayer.bronze and ds.source_id}
    bronze_sources = db.query(DataSource).filter(DataSource.id.in_(bronze_source_ids)).all() if bronze_source_ids else []

    for source in bronze_sources:
        await _upsert(source, dag_render.conn_id_for_source(source.id))

    # 3. render (always the BASE, unsuffixed dag_id — §2: never bake an environment-suffixed
    # id into what gets captured) + deposit the DAG under this binding's actual, suffixed name.
    base_dag_content = dag_render.render_dag(project, datasets, object_store, target.dbt_bin)
    base_dag_id = dag_render.dag_id_for_project(project)
    deployed_dag_id = dag_render.dag_id_for_environment(project, environment)
    deployed_dag_content = dag_render.apply_environment_suffix(base_dag_content, project, environment)
    dag_path = posixpath.join(target.dags_dir, f"{deployed_dag_id}.py")

    # A prior build/promotion of THIS SAME binding under a different dag_id (e.g. built once
    # before the environment-suffix convention existed) leaves an orphaned file behind under
    # the old name — Airflow would otherwise show both forever. Best-effort: never blocks
    # deployment of the current one.
    if binding.dag_file_path and binding.dag_file_path != dag_path:
        def _delete_stale_dag():
            try:
                with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
                    ssh.run_command(client, f"rm -f {binding.dag_file_path}")
            except Exception:
                pass
        await run_in_threadpool(_delete_stale_dag)

    await run_in_threadpool(_deploy_dag_via_ssh, target, dag_path, deployed_dag_content)

    # 4. best-effort activation (Module 3 correctif, étape 2) — the file was just deposited;
    # Airflow's dag-processor almost never has parsed it yet this soon (immediate reparse via
    # the API isn't reliable — see api/routes/airflow.py's own note on this), so this
    # attempt frequently no-ops with a 404 here. That's expected, not a failure: the real,
    # reliable activation point is get_deploy_status()'s own retry, fired once the DAG is
    # actually known to Airflow. Never blocks or fails the build either way.
    activated = None
    activation_error = None
    if binding.schedule:
        try:
            await airflow_api.set_dag_paused(base_url, admin_user, admin_password, deployed_dag_id, False)
            activated = True
        except airflow_api.AirflowAPIError as exc:
            activated = False
            activation_error = str(exc)

    return {
        "dbt_files": list(dbt_files.keys()),
        "connections_created": connections_created,
        # what's actually LIVE now — goes on binding.dag_id/dag_file_path
        "dag_id": deployed_dag_id,
        "dag_path": dag_path,
        # what gets CAPTURED into a MedallionVersion — always base/unsuffixed (§2). Same
        # dbt_files either way (dbt_project.yml/profiles.yml/models carry no dag_id at all).
        "dag_id_base": base_dag_id,
        "dag_content": base_dag_content,
        "dbt_files_content": dbt_files,
        "activated": activated,
        "activation_error": activation_error,
    }
