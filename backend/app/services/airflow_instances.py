"""Single resolution point for turning an AirflowInstance (platform or external) into
either a pure API config (base_url/credentials) or a full deploy target (+ SSH/paths).
Everything downstream (airflow_api.py, dag_render.py, dbt_project.py, ingest) stays
unaware of where the instance came from — only this module knows.
"""
import posixpath
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.airflow_instance import AirflowInstance, AirflowInstanceOrigin, AirflowInstanceStatus
from app.models.infra_stack import InfraStack
from app.models.medallion import MedallionProject
from app.models.server import Server
from app.services import airflow_api
from app.services.compose import stack_proj_dir
from app.services.dag_render import DBT_BIN as PLATFORM_DBT_BIN

# Airflow < 3.0.6 has no /auth/token (pre-Airflow-3 auth is an entirely different
# mechanism) — this is the version the platform provisions itself (Module 1), the only
# terrain actually validated. Kept as a single named constant, never hard-coded elsewhere.
AIRFLOW_MIN_VERSION = "3.0.6"

# The real, unchanged dag_render.dag_id_for_project() convention — reused as-is here
# rather than introducing a new prefix, since dag_render.py stays untouched by this module.
_MANAGED_DAG_PREFIX = "medallion_"


class AirflowInstanceError(Exception):
    pass


def parse_version(raw: str) -> tuple[int, ...]:
    """Semantic (major, minor, patch) tuple comparison — '3.0.10' > '3.0.6' must hold,
    which a lexical string comparison would get wrong."""
    parts = []
    for chunk in raw.strip().split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def meets_min_version(raw: str) -> bool:
    return parse_version(raw) >= parse_version(AIRFLOW_MIN_VERSION)


def is_platform_dag(dag_id: str) -> bool:
    """Only DAGs the control plane itself generated are pilotable (pause/trigger) —
    everything else on an external Airflow is observed, never touched."""
    return dag_id.startswith(_MANAGED_DAG_PREFIX)


@dataclass
class AirflowConfig:
    base_url: str
    username: str
    password: str
    verify_tls: bool


def get_airflow_config(instance: AirflowInstance) -> AirflowConfig:
    return AirflowConfig(
        base_url=instance.base_url,
        username=instance.username,
        password=decrypt_secret(instance.secret_encrypted),
        verify_tls=instance.verify_tls,
    )


async def test_instance_connection(config: AirflowConfig) -> tuple[AirflowInstanceStatus, str, str | None, str, dict]:
    """Reachability + version (semantic, ≥ AIRFLOW_MIN_VERSION) + credentials, in that
    order — mirrors the shape of services/ssh.py's test_connection (status, message, extra).

    Returns (status, message, airflow_version, message_key, message_params). `message` is
    a ready-to-display French string (used as-is by the plain connection-test endpoint's
    toast); `message_key`/`message_params` are the same result structured for the
    preflight checklist, which renders it in whichever language the UI is set to.
    """
    try:
        await airflow_api.get_token(config.base_url, config.username, config.password, force_refresh=True)
    except airflow_api.AirflowAPIError as exc:
        if exc.status_code == 404:
            return (
                AirflowInstanceStatus.unsupported_version,
                f"Point d'API v2 introuvable (/auth/token) — la plateforme requiert Airflow ≥ {AIRFLOW_MIN_VERSION}.",
                None,
                "api_v2_no_v2",
                {"min": AIRFLOW_MIN_VERSION},
            )
        if exc.status_code in (401, 403):
            return (
                AirflowInstanceStatus.unreachable,
                "Authentification refusée : vérifiez le nom d'utilisateur et le mot de passe.",
                None,
                "api_v2_auth_failed",
                {},
            )
        return AirflowInstanceStatus.unreachable, str(exc), None, "raw", {"value": str(exc)}

    try:
        version = await airflow_api.get_version(config.base_url, config.username, config.password)
    except airflow_api.AirflowAPIError as exc:
        return (
            AirflowInstanceStatus.unreachable,
            f"Authentifié, mais version illisible : {exc}",
            None,
            "api_v2_version_unreadable",
            {"error": str(exc)},
        )

    if not version or not meets_min_version(version):
        detected = version or "inconnue"
        return (
            AirflowInstanceStatus.unsupported_version,
            f"version {detected} détectée — la plateforme requiert Airflow ≥ {AIRFLOW_MIN_VERSION}.",
            version or None,
            "api_v2_unsupported",
            {"version": detected, "min": AIRFLOW_MIN_VERSION},
        )
    return (
        AirflowInstanceStatus.reachable,
        f"Connexion réussie — Airflow {version}.",
        version,
        "api_v2_ok",
        {"version": version},
    )


@dataclass
class DeployTarget:
    """Resolved connection needed to deposit dbt/DAG files and read run artifacts —
    same shape whether the instance is platform (via its InfraStack's Server) or
    external (via deploy_access); only the provenance of the paths differs."""
    airflow: AirflowConfig
    ssh_host: str
    ssh_port: int
    ssh_user: str
    ssh_auth_method: str
    ssh_secret: str
    dags_dir: str
    dbt_dir: str
    plugins_dir: str
    dbt_bin: str
    is_platform: bool
    stack_id: int | None  # platform only — used for bronze source co-location resolution


def ensure_deployable(instance: AirflowInstance) -> None:
    if instance.origin == AirflowInstanceOrigin.external and not (instance.capabilities or {}).get("deployable"):
        raise AirflowInstanceError(
            "Cette instance Airflow externe n'est pas déployable : lancez le préflight "
            "depuis /orchestrators pour vérifier l'accès SFTP, dbt et les dépendances."
        )


def resolve_deploy_target(db: Session, instance: AirflowInstance, dbt_project_name: str) -> DeployTarget:
    ensure_deployable(instance)
    airflow_conf = get_airflow_config(instance)

    if instance.origin == AirflowInstanceOrigin.platform:
        if instance.stack_id is None:
            raise AirflowInstanceError("Instance Airflow platform sans stack associée.")
        stack = db.get(InfraStack, instance.stack_id)
        if stack is None:
            raise AirflowInstanceError("Stack Airflow introuvable pour cette instance.")
        server = db.get(Server, stack.server_id)
        if server is None:
            raise AirflowInstanceError("Serveur introuvable pour cette stack.")
        proj_dir = stack_proj_dir(stack.id)
        return DeployTarget(
            airflow=airflow_conf,
            ssh_host=server.hostname,
            ssh_port=server.ssh_port,
            ssh_user=server.ssh_user,
            ssh_auth_method=server.auth_method.value,
            ssh_secret=decrypt_secret(server.secret_encrypted),
            dags_dir=f"{proj_dir}/dags",
            dbt_dir=f"{proj_dir}/dbt/{dbt_project_name}",
            plugins_dir=f"{proj_dir}/plugins",
            dbt_bin=PLATFORM_DBT_BIN,
            is_platform=True,
            stack_id=stack.id,
        )

    deploy_access = instance.deploy_access
    if not deploy_access:
        raise AirflowInstanceError("Cette instance Airflow externe n'a pas d'accès de déploiement configuré.")
    dags_path = deploy_access["dags_path"].rstrip("/")
    return DeployTarget(
        airflow=airflow_conf,
        ssh_host=deploy_access["ssh_host"],
        ssh_port=deploy_access.get("ssh_port", 22),
        ssh_user=deploy_access["ssh_user"],
        ssh_auth_method=deploy_access["auth_method"],
        ssh_secret=decrypt_secret(deploy_access["secret_encrypted"]),
        dags_dir=dags_path,
        dbt_dir=f"{deploy_access['dbt_path'].rstrip('/')}/{dbt_project_name}",
        # Standard Airflow layout: dags/ and plugins/ are siblings under AIRFLOW_HOME.
        plugins_dir=posixpath.join(posixpath.dirname(dags_path), "plugins"),
        dbt_bin=deploy_access.get("dbt_bin") or "dbt",
        is_platform=False,
        stack_id=None,
    )


def resolve_project_instance(db: Session, project: MedallionProject) -> AirflowInstance:
    instance = db.get(AirflowInstance, project.airflow_instance_id)
    if instance is None:
        raise AirflowInstanceError("Instance Airflow introuvable pour ce projet.")
    return instance
