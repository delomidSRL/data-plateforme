from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import decrypt_secret
from app.db.session import get_db
from app.models.data_source import DataSource, DataSourceType
from app.models.infra_stack import InfraStack
from app.models.server import Server
from app.models.user import User
from app.schemas.airflow import ConnectionCreateRequest, ConnectionTestRequest, DagDeployRequest, DagPauseRequest, DagRunTriggerRequest
from app.services import airflow_api, ssh
from app.services.stack_secrets import decrypt_services

router = APIRouter(prefix="/api/servers/{server_id}/stacks/{sid}/airflow", tags=["airflow"])

_CONN_TYPE_MAP = {
    DataSourceType.postgresql: "postgres",
    DataSourceType.mysql: "mysql",
    DataSourceType.oracle: "oracle",
    DataSourceType.minio: "aws",
}


def _resolve(db: Session, server_id: int, sid: int) -> tuple[Server, InfraStack, dict]:
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serveur introuvable.")
    stack = db.query(InfraStack).filter(InfraStack.id == sid, InfraStack.server_id == server_id).first()
    if stack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stack introuvable.")

    airflow_conf = (decrypt_services(stack.services).get("airflow") or {})
    if not airflow_conf.get("enabled"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Airflow n'est pas activé sur cette stack.")

    return server, stack, airflow_conf


def _base_url(server: Server, airflow_conf: dict) -> str:
    return f"http://{server.hostname}:{airflow_conf['web_port']}"


async def _call(fn, *args, **kwargs):
    try:
        return await fn(*args, **kwargs)
    except airflow_api.AirflowAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/token")
async def get_airflow_token(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    await _call(airflow_api.get_token, _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"], True)
    return {"message": "Token Airflow généré et mis en cache."}


@router.get("/connections")
async def list_connections(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    return await _call(airflow_api.list_connections, _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"])


@router.post("/connections", status_code=status.HTTP_201_CREATED)
async def create_connection(server_id: int, sid: int, payload: ConnectionCreateRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)

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
        _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"],
        connection_id=payload.connection_id, conn_type=conn_type, host=host, login=login,
        conn_password=password, schema=schema, port=port, extra=payload.extra, description=payload.description,
    )


@router.post("/connections/test")
async def test_connection(server_id: int, sid: int, payload: ConnectionTestRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    return await _call(
        airflow_api.test_connection,
        _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"],
        conn_type=payload.conn_type, host=payload.host, login=payload.login,
        conn_password=payload.password, schema=payload.schema_, port=payload.port, extra=payload.extra,
    )


@router.post("/dags", status_code=status.HTTP_201_CREATED)
def deploy_dag_file(server_id: int, sid: int, payload: DagDeployRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    from app.services.compose import stack_proj_dir

    secret = decrypt_secret(server.secret_encrypted)
    remote_path = f"{stack_proj_dir(stack.id)}/dags/{payload.filename}"
    try:
        ssh.write_remote_file(server.hostname, server.ssh_port, server.ssh_user, server.auth_method.value, secret, remote_path, payload.content)
    except ssh.SSHError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return {
        "message": "Fichier DAG déposé. Il sera détecté par le dag-processor au prochain scan (les tentatives de reparse immédiat via l'API ne sont pas fiables sans le file_token signé par Airflow).",
        "path": remote_path,
    }


@router.get("/dags")
async def list_dags(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    return await _call(airflow_api.list_dags, _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"])


@router.patch("/dags/{dag_id}")
async def pause_dag(server_id: int, sid: int, dag_id: str, payload: DagPauseRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    return await _call(airflow_api.set_dag_paused, _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"], dag_id, payload.is_paused)


@router.post("/dags/{dag_id}/runs", status_code=status.HTTP_201_CREATED)
async def trigger_dag_run(server_id: int, sid: int, dag_id: str, payload: DagRunTriggerRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    return await _call(
        airflow_api.trigger_dag_run, _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"],
        dag_id, payload.logical_date, payload.conf,
    )


@router.get("/dags/{dag_id}/runs/{run_id}")
async def get_dag_run(server_id: int, sid: int, dag_id: str, run_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server, stack, airflow_conf = _resolve(db, server_id, sid)
    return await _call(airflow_api.get_dag_run, _base_url(server, airflow_conf), airflow_conf["admin_user"], airflow_conf["admin_password"], dag_id, run_id)
