from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.security import decrypt_secret, encrypt_secret
from app.db.session import get_db
from app.models.airflow_instance import AirflowInstance, AirflowInstanceOrigin
from app.models.data_source import DataSource, DataSourceType
from app.models.project_environment_binding import ProjectEnvironmentBinding
from app.models.user import User
from app.schemas.airflow import ConnectionCreateRequest, ConnectionTestRequest, DagPauseRequest, DagRunTriggerRequest
from app.schemas.airflow_instance import (
    AirflowInstanceCreate,
    AirflowInstanceOut,
    AirflowInstanceTestRequest,
    AirflowInstanceTestResult,
    AirflowInstanceUpdate,
    DagOut,
    DeployAccessUpdate,
    PreflightResultOut,
)
from app.services import airflow_api, airflow_instances, airflow_preflight
from app.services.airflow_instances import get_airflow_config, is_platform_dag, test_instance_connection

PREFLIGHT_STALE_AFTER_DAYS = 7

router = APIRouter(prefix="/api/airflow-instances", tags=["airflow-instances"])

_CONN_TYPE_MAP = {
    DataSourceType.postgresql: "postgres",
    DataSourceType.mysql: "mysql",
    DataSourceType.oracle: "oracle",
    DataSourceType.minio: "aws",
}


def _get_instance(db: Session, iid: int) -> AirflowInstance:
    instance = db.get(AirflowInstance, iid)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance Airflow introuvable.")
    return instance


def _ensure_external(instance: AirflowInstance) -> None:
    if instance.origin != AirflowInstanceOrigin.external:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Les instances gérées par la plateforme ne sont pas modifiables ici.")


async def _call(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except airflow_api.AirflowAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/", response_model=list[AirflowInstanceOut])
def list_instances(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(AirflowInstance).order_by(AirflowInstance.created_at.asc()).all()


@router.post("/", response_model=AirflowInstanceOut, status_code=status.HTTP_201_CREATED)
def create_instance(payload: AirflowInstanceCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    instance = AirflowInstance(
        name=payload.name,
        origin=AirflowInstanceOrigin.external,
        base_url=payload.base_url.rstrip("/"),
        username=payload.username,
        secret_encrypted=encrypt_secret(payload.password),
        verify_tls=payload.verify_tls,
        capabilities={"deployable": False},
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/test", response_model=AirflowInstanceTestResult)
async def test_instance_adhoc(payload: AirflowInstanceTestRequest, _: User = Depends(get_current_user)):
    config = airflow_instances.AirflowConfig(base_url=payload.base_url.rstrip("/"), username=payload.username, password=payload.password, verify_tls=payload.verify_tls)
    new_status, message, version, _msg_key, _msg_params = await test_instance_connection(config)
    return AirflowInstanceTestResult(reachable=new_status.value == "reachable", message=message, airflow_version=version, status=new_status)


@router.get("/{iid}", response_model=AirflowInstanceOut)
def get_instance(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _get_instance(db, iid)


@router.put("/{iid}", response_model=AirflowInstanceOut)
def update_instance(iid: int, payload: AirflowInstanceUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    instance = _get_instance(db, iid)
    _ensure_external(instance)
    data = payload.model_dump(exclude_unset=True)
    if data.get("password"):
        instance.secret_encrypted = encrypt_secret(data.pop("password"))
    else:
        data.pop("password", None)
    if "base_url" in data and data["base_url"]:
        data["base_url"] = data["base_url"].rstrip("/")
    for field, value in data.items():
        setattr(instance, field, value)
    db.commit()
    db.refresh(instance)
    return instance


@router.delete("/{iid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance(iid: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    instance = _get_instance(db, iid)
    _ensure_external(instance)
    # Module 17 — airflow_instance_id moved from MedallionProject to ProjectEnvironmentBinding
    # (one per environment); a project counts once even if both its dev and prod bindings
    # happen to reference the same instance.
    linked = (
        db.query(ProjectEnvironmentBinding.project_id)
        .filter(ProjectEnvironmentBinding.airflow_instance_id == iid)
        .distinct()
        .count()
    )
    if linked:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{linked} projet(s) médaillon utilisent cette instance — impossible de la supprimer.")
    db.delete(instance)
    db.commit()


@router.put("/{iid}/deploy-access", response_model=AirflowInstanceOut)
def set_deploy_access(iid: int, payload: DeployAccessUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    instance = _get_instance(db, iid)
    _ensure_external(instance)

    existing = instance.deploy_access or {}
    secret_plain = payload.secret or (decrypt_secret(existing["secret_encrypted"]) if existing.get("secret_encrypted") else None)
    if not secret_plain:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Un secret SSH (mot de passe ou clé) est requis.")

    instance.deploy_access = {
        "ssh_host": payload.ssh_host,
        "ssh_port": payload.ssh_port,
        "ssh_user": payload.ssh_user,
        "auth_method": payload.auth_method,
        "secret_encrypted": encrypt_secret(secret_plain),
        "dags_path": payload.dags_path.rstrip("/"),
        "dbt_path": payload.dbt_path.rstrip("/"),
        "dbt_bin": payload.dbt_bin,
        "exec_prefix": payload.exec_prefix,
    }
    # The config just changed — any previous preflight result no longer reflects reality,
    # so deployability must be re-earned via a fresh preflight rather than carried over.
    instance.capabilities = {"deployable": False}
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/{iid}/preflight", response_model=PreflightResultOut)
async def trigger_preflight(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    if instance.origin != AirflowInstanceOrigin.external:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le préflight ne s'applique qu'aux instances externes.")

    capabilities = await airflow_preflight.run_preflight(instance)
    instance.capabilities = capabilities
    instance.last_checked_at = datetime.now(timezone.utc)
    db.commit()

    return PreflightResultOut(deployable=capabilities["deployable"], checks=capabilities["checks"], checked_at=datetime.fromisoformat(capabilities["checked_at"]), stale=False)


@router.get("/{iid}/preflight", response_model=PreflightResultOut)
def get_preflight(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    capabilities = instance.capabilities or {}
    checks = capabilities.get("checks", [])
    checked_at_raw = capabilities.get("checked_at")
    checked_at = datetime.fromisoformat(checked_at_raw) if checked_at_raw else None
    stale = bool(checked_at) and (datetime.now(timezone.utc) - checked_at).days > PREFLIGHT_STALE_AFTER_DAYS
    return PreflightResultOut(deployable=bool(capabilities.get("deployable")), checks=checks, checked_at=checked_at, stale=stale)


@router.post("/{iid}/test", response_model=AirflowInstanceTestResult)
async def test_instance(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    new_status, message, version, _msg_key, _msg_params = await test_instance_connection(config)

    instance.status = new_status
    instance.last_checked_at = datetime.now(timezone.utc)
    if version:
        instance.airflow_version = version
    db.commit()

    return AirflowInstanceTestResult(reachable=new_status.value == "reachable", message=message, airflow_version=version, status=new_status)


@router.get("/{iid}/dags", response_model=list[DagOut])
async def list_dags(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    dags = await _call(airflow_api.list_dags, config.base_url, config.username, config.password)
    return [
        DagOut(dag_id=d["dag_id"], is_paused=bool(d.get("is_paused")), managed=is_platform_dag(d["dag_id"]))
        for d in dags
    ]


@router.patch("/{iid}/dags/{dag_id}")
async def pause_dag(iid: int, dag_id: str, payload: DagPauseRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if not is_platform_dag(dag_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce DAG n'est pas géré par la plateforme — pilotage refusé.")
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    return await _call(airflow_api.set_dag_paused, config.base_url, config.username, config.password, dag_id, payload.is_paused)


@router.post("/{iid}/dags/{dag_id}/runs", status_code=status.HTTP_201_CREATED)
async def trigger_dag_run(iid: int, dag_id: str, payload: DagRunTriggerRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if not is_platform_dag(dag_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce DAG n'est pas géré par la plateforme — déclenchement refusé.")
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    return await _call(airflow_api.trigger_dag_run, config.base_url, config.username, config.password, dag_id, payload.logical_date, payload.conf)


@router.get("/{iid}/dags/{dag_id}/runs")
async def list_dag_runs(iid: int, dag_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    return await _call(airflow_api.list_dag_runs, config.base_url, config.username, config.password, dag_id)


@router.get("/{iid}/dags/{dag_id}/runs/{run_id}")
async def get_dag_run(iid: int, dag_id: str, run_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    return await _call(airflow_api.get_dag_run, config.base_url, config.username, config.password, dag_id, run_id)


@router.get("/{iid}/connections")
async def list_connections(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    return await _call(airflow_api.list_connections, config.base_url, config.username, config.password)


@router.post("/{iid}/connections", status_code=status.HTTP_201_CREATED)
async def create_connection(iid: int, payload: ConnectionCreateRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)

    host, login, password, port, schema, conn_type = payload.host, payload.login, payload.password, payload.port, payload.schema_, payload.conn_type

    if payload.from_data_source_id is not None:
        source = db.get(DataSource, payload.from_data_source_id)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source introuvable.")
        host = source.host
        login = source.username
        password = decrypt_secret(source.secret_encrypted)
        port = source.port
        schema = source.database_name
        conn_type = _CONN_TYPE_MAP.get(source.type, conn_type)

    return await _call(
        airflow_api.create_connection,
        config.base_url, config.username, config.password,
        connection_id=payload.connection_id, conn_type=conn_type, host=host, login=login,
        conn_password=password, schema=schema, port=port, extra=payload.extra, description=payload.description,
    )


@router.post("/{iid}/connections/test")
async def test_connection(iid: int, payload: ConnectionTestRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_airflow_config(instance)
    return await _call(
        airflow_api.test_connection,
        config.base_url, config.username, config.password,
        conn_type=payload.conn_type, host=payload.host, login=payload.login,
        conn_password=payload.password, schema=payload.schema_, port=payload.port, extra=payload.extra,
    )
