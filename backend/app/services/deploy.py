from sqlalchemy.orm import Session

from app.core.security import decrypt_secret, encrypt_secret
from app.models.airflow_instance import AirflowInstance, AirflowInstanceOrigin, AirflowInstanceStatus
from app.models.data_source import DataSource, DataSourceOrigin, DataSourceStatus, DataSourceType
from app.models.infra_stack import InfraStack, StackStatus
from app.models.server import Server
from app.models.superset_instance import SupersetInstance, SupersetInstanceOrigin, SupersetInstanceStatus
from app.services import ssh
from app.services.compose import AIRFLOW_IMAGE_TAG, JUPYTER_IMAGE_TAG, SUPERSET_IMAGE_TAG, render_compose, stack_proj_dir
from app.services.monitor import ContainerStatus, parse_compose_ps
from app.services.stack_secrets import decrypt_services
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

BUILD_TIMEOUT = 900

_env = Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "templates")))


def _server_secret(server: Server) -> str:
    return decrypt_secret(server.secret_encrypted)


def _compose_cmd(stack: InfraStack) -> str:
    proj_dir = stack_proj_dir(stack.id)
    return f"docker compose -f {proj_dir}/docker-compose.yml -p dataplateforme-stack-{stack.id}"


def deploy_stack(db: Session, stack: InfraStack, server: Server) -> dict:
    secret = _server_secret(server)
    proj_dir = stack_proj_dir(stack.id)
    decrypted = decrypt_services(stack.services)
    airflow_conf = decrypted.get("airflow") or {}
    superset_conf = decrypted.get("superset") or {}
    jupyter_conf = decrypted.get("jupyter") or {}
    needs_airflow_build = bool(airflow_conf.get("enabled")) and airflow_conf.get("dbt_enabled", True)

    with ssh.ssh_session(server.hostname, server.ssh_port, server.ssh_user, server.auth_method.value, secret) as client:
        ssh.run_command(client, f"mkdir -p {proj_dir}/dags {proj_dir}/logs {proj_dir}/config {proj_dir}/plugins {proj_dir}/dbt {proj_dir}/superset {proj_dir}/jupyter {proj_dir}/notebooks")
        if jupyter_conf.get("enabled"):
            # Files land on disk owned by the SSH user, but the Jupyter container runs as
            # its own fixed uid (jovyan) which rarely matches — see the identical dbt/
            # ownership bug this same pattern caused for Airflow's per-project directories.
            ssh.run_command(client, f"chmod -R o+rwX {proj_dir}/notebooks")

        if superset_conf.get("enabled"):
            superset_config = _env.get_template("compose/superset_config.py.j2").render()
            superset_dockerfile = _env.get_template("superset/Dockerfile.j2").render()
            sftp = client.open_sftp()
            try:
                with sftp.file(f"{proj_dir}/superset/superset_config.py", "w") as f:
                    f.write(superset_config)
                with sftp.file(f"{proj_dir}/superset/Dockerfile", "w") as f:
                    f.write(superset_dockerfile)
            finally:
                sftp.close()
            build_result = ssh.run_command(client, f"cd {proj_dir}/superset && docker build -t {SUPERSET_IMAGE_TAG} .", timeout=BUILD_TIMEOUT)
            if not build_result.ok:
                stack.status = StackStatus.error
                stack.last_error = f"Échec de build de l'image Superset custom : {build_result.stderr[-2000:]}"
                db.commit()
                raise RuntimeError(stack.last_error)

        if jupyter_conf.get("enabled"):
            jupyter_dockerfile = _env.get_template("jupyter/Dockerfile.j2").render()
            sftp = client.open_sftp()
            try:
                with sftp.file(f"{proj_dir}/jupyter/Dockerfile", "w") as f:
                    f.write(jupyter_dockerfile)
            finally:
                sftp.close()
            build_result = ssh.run_command(client, f"cd {proj_dir}/jupyter && docker build -t {JUPYTER_IMAGE_TAG} .", timeout=BUILD_TIMEOUT)
            if not build_result.ok:
                stack.status = StackStatus.error
                stack.last_error = f"Échec de build de l'image Jupyter custom : {build_result.stderr[-2000:]}"
                db.commit()
                raise RuntimeError(stack.last_error)

        if needs_airflow_build:
            dockerfile = _env.get_template("airflow/Dockerfile.j2").render()
            sftp = client.open_sftp()
            try:
                with sftp.file(f"{proj_dir}/Dockerfile", "w") as f:
                    f.write(dockerfile)
            finally:
                sftp.close()
            build_result = ssh.run_command(client, f"cd {proj_dir} && docker build -t {AIRFLOW_IMAGE_TAG} .", timeout=BUILD_TIMEOUT)
            if not build_result.ok:
                stack.status = StackStatus.error
                stack.last_error = f"Échec de build de l'image Airflow custom : {build_result.stderr[-2000:]}"
                db.commit()
                raise RuntimeError(stack.last_error)

        compose_yaml = render_compose(stack)
        sftp = client.open_sftp()
        try:
            with sftp.file(f"{proj_dir}/docker-compose.yml", "w") as f:
                f.write(compose_yaml)
        finally:
            sftp.close()

        up_result = ssh.run_command(client, f"{_compose_cmd(stack)} up -d --force-recreate --remove-orphans", timeout=BUILD_TIMEOUT)
        if not up_result.ok:
            stack.status = StackStatus.error
            stack.last_error = f"Échec du déploiement : {up_result.stderr[-2000:]}"
            db.commit()
            raise RuntimeError(stack.last_error)

        ps_result = ssh.run_command(client, f"{_compose_cmd(stack)} ps --format json")
        containers = parse_compose_ps(ps_result.stdout)

    registered = _register_platform_sources(db, stack, server, decrypted)
    _register_platform_airflow(db, stack, server, decrypted)
    _register_platform_superset(db, stack, server, decrypted)

    stack.status = StackStatus.running
    stack.compose_path = f"{proj_dir}/docker-compose.yml"
    stack.last_error = None
    from datetime import datetime, timezone
    stack.last_deployed_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "containers": [c.name for c in containers],
        "registered_sources": registered,
    }


def _register_platform_sources(db: Session, stack: InfraStack, server: Server, decrypted_services: dict) -> list[str]:
    registered = []

    postgres = decrypted_services.get("postgres")
    if postgres and postgres.get("enabled"):
        existing = db.query(DataSource).filter(DataSource.stack_id == stack.id, DataSource.type == DataSourceType.postgresql).first()
        source = existing or DataSource(stack_id=stack.id, origin=DataSourceOrigin.platform, type=DataSourceType.postgresql)
        source.name = f"{stack.name} · PostgreSQL"
        source.host = server.hostname
        source.port = postgres["port"]
        source.database_name = postgres["db_name"]
        source.username = postgres["user"]
        source.secret_encrypted = encrypt_secret(postgres["password"])
        source.status = DataSourceStatus.unknown
        db.add(source)
        registered.append(source.name)

    minio = decrypted_services.get("minio")
    if minio and minio.get("enabled"):
        existing = db.query(DataSource).filter(DataSource.stack_id == stack.id, DataSource.type == DataSourceType.minio).first()
        source = existing or DataSource(stack_id=stack.id, origin=DataSourceOrigin.platform, type=DataSourceType.minio)
        source.name = f"{stack.name} · MinIO"
        source.host = server.hostname
        source.port = minio["api_port"]
        source.database_name = None
        source.username = minio["access_key"]
        source.secret_encrypted = encrypt_secret(minio["secret_key"])
        source.options = {"secure": False, "region": "us-east-1"}
        source.status = DataSourceStatus.unknown
        db.add(source)
        registered.append(source.name)

    db.flush()
    return registered


def _register_platform_airflow(db: Session, stack: InfraStack, server: Server, decrypted_services: dict) -> AirflowInstance | None:
    """Same create-or-reuse-by-stack_id pattern as _register_platform_sources, but
    deactivated (not deleted) on teardown, never created — a MedallionProject's
    airflow_instance_id must never dangle across a stop/redeploy cycle."""
    airflow_conf = decrypted_services.get("airflow")
    if not airflow_conf or not airflow_conf.get("enabled"):
        return None

    existing = db.query(AirflowInstance).filter(AirflowInstance.stack_id == stack.id).first()
    instance = existing or AirflowInstance(stack_id=stack.id, origin=AirflowInstanceOrigin.platform)
    instance.name = f"{stack.name} (Airflow)"
    instance.base_url = f"http://{server.hostname}:{airflow_conf['web_port']}"
    instance.username = airflow_conf["admin_user"]
    instance.secret_encrypted = encrypt_secret(airflow_conf["admin_password"])
    instance.capabilities = {"deployable": True}
    instance.status = AirflowInstanceStatus.unknown
    instance.is_active = True
    db.add(instance)
    db.flush()
    return instance


def _register_platform_superset(db: Session, stack: InfraStack, server: Server, decrypted_services: dict) -> SupersetInstance | None:
    """Same create-or-reuse-by-stack_id pattern as _register_platform_airflow — deactivated
    (not deleted) on teardown, so a publication's reference to it never dangles across a
    stop/redeploy cycle."""
    superset_conf = decrypted_services.get("superset")
    if not superset_conf or not superset_conf.get("enabled"):
        return None

    existing = db.query(SupersetInstance).filter(SupersetInstance.stack_id == stack.id).first()
    instance = existing or SupersetInstance(stack_id=stack.id, origin=SupersetInstanceOrigin.platform)
    instance.name = f"{stack.name} (Superset)"
    instance.base_url = f"http://{server.hostname}:{superset_conf['port']}"
    instance.admin_username = superset_conf["admin_user"]
    instance.secret_encrypted = encrypt_secret(superset_conf["admin_password"])
    instance.status = SupersetInstanceStatus.unknown
    instance.is_active = True
    db.add(instance)
    db.flush()
    return instance


def stop_stack(server: Server, stack: InfraStack) -> None:
    secret = _server_secret(server)
    with ssh.ssh_session(server.hostname, server.ssh_port, server.ssh_user, server.auth_method.value, secret) as client:
        ssh.run_command(client, f"{_compose_cmd(stack)} stop", timeout=BUILD_TIMEOUT)


def restart_stack(server: Server, stack: InfraStack) -> None:
    secret = _server_secret(server)
    with ssh.ssh_session(server.hostname, server.ssh_port, server.ssh_user, server.auth_method.value, secret) as client:
        ssh.run_command(client, f"{_compose_cmd(stack)} restart", timeout=BUILD_TIMEOUT)


def teardown_stack(db: Session, server: Server, stack: InfraStack) -> None:
    secret = _server_secret(server)
    with ssh.ssh_session(server.hostname, server.ssh_port, server.ssh_user, server.auth_method.value, secret) as client:
        ssh.run_command(client, f"{_compose_cmd(stack)} down", timeout=BUILD_TIMEOUT)
    db.query(DataSource).filter(DataSource.stack_id == stack.id).delete()
    db.query(AirflowInstance).filter(AirflowInstance.stack_id == stack.id).update({"is_active": False, "status": AirflowInstanceStatus.unreachable})
    db.query(SupersetInstance).filter(SupersetInstance.stack_id == stack.id).update({"is_active": False, "status": SupersetInstanceStatus.unreachable})
    db.commit()


def get_status(server: Server, stack: InfraStack) -> list[ContainerStatus]:
    secret = _server_secret(server)
    with ssh.ssh_session(server.hostname, server.ssh_port, server.ssh_user, server.auth_method.value, secret) as client:
        ps_result = ssh.run_command(client, f"{_compose_cmd(stack)} ps --format json")
        return parse_compose_ps(ps_result.stdout)


def verify_stack(db: Session, stack: InfraStack, server: Server) -> dict:
    """Re-checks the real container states on the server and reconciles stack.status with
    them. Recovery path for when `deploy_stack`'s own `up` wait reports a transient failure
    (e.g. a dependent service briefly unhealthy while it finishes starting) even though every
    container settles into a healthy/running state moments later — `up`'s exit code alone
    can't tell the two apart, so this re-derives the status from what's actually running.

    When a stack's very first `deploy_stack` call failed at the `up` step, it raised before
    ever reaching `_register_platform_*` below — so no DataSource/AirflowInstance/
    SupersetInstance row exists yet, even once the containers settle and this function marks
    the stack running. Re-running the same registration here (safe: it's the same
    create-or-reuse-by-stack_id pattern `deploy_stack` uses) is what makes those platform
    resources show up in Orchestrateurs / Superset / the médaillon project wizard afterward."""
    containers = get_status(server, stack)

    checks = []
    all_ok = bool(containers)
    for c in containers:
        ok = c.state == "running" and c.health in (None, "healthy")
        all_ok = all_ok and ok
        checks.append({
            "name": c.name,
            "service": c.service,
            "state": c.state,
            "health": c.health,
            "ok": ok,
            "detail": c.health or c.status or c.state,
        })

    from datetime import datetime, timezone

    if all_ok:
        decrypted = decrypt_services(stack.services)
        _register_platform_sources(db, stack, server, decrypted)
        _register_platform_airflow(db, stack, server, decrypted)
        _register_platform_superset(db, stack, server, decrypted)
        stack.status = StackStatus.running
        stack.last_error = None
        stack.last_deployed_at = datetime.now(timezone.utc)
    else:
        stack.status = StackStatus.error
        failing = ", ".join(c["name"] for c in checks if not c["ok"]) or "aucun conteneur détecté"
        stack.last_error = f"Vérification manuelle : service(s) non opérationnel(s) — {failing}"
    db.commit()

    return {"ok": all_ok, "status": stack.status.value, "services": checks}
