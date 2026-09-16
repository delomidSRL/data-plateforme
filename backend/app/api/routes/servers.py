from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import decrypt_secret, encrypt_secret
from app.db.session import get_db
from app.models.server import Server, ServerStatus
from app.models.user import User
from app.schemas.server import ServerCreate, ServerOut, ServerTestResult, ServerUpdate
from app.services import ssh

router = APIRouter(prefix="/api/servers", tags=["servers"])


@router.get("/", response_model=list[ServerOut])
def list_servers(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Server).order_by(Server.created_at.asc()).all()


@router.post("/", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
def create_server(payload: ServerCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = Server(
        name=payload.name,
        hostname=payload.hostname,
        ssh_port=payload.ssh_port,
        ssh_user=payload.ssh_user,
        auth_method=payload.auth_method,
        secret_encrypted=encrypt_secret(payload.secret),
        environment=payload.environment,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@router.post("/test", response_model=ServerTestResult)
def test_server_adhoc(payload: ServerCreate, _: User = Depends(get_current_user)):
    reachable, message, docker_version = ssh.test_connection(payload.hostname, payload.ssh_port, payload.ssh_user, payload.auth_method.value, payload.secret)
    return ServerTestResult(reachable=reachable, message=message, docker_version=docker_version)


@router.get("/{server_id}", response_model=ServerOut)
def get_server(server_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serveur introuvable.")
    return server


@router.put("/{server_id}", response_model=ServerOut)
def update_server(server_id: int, payload: ServerUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serveur introuvable.")

    data = payload.model_dump(exclude_unset=True, exclude={"secret"})
    for field, value in data.items():
        setattr(server, field, value)
    if payload.secret:
        server.secret_encrypted = encrypt_secret(payload.secret)

    db.commit()
    db.refresh(server)
    return server


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server(server_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serveur introuvable.")
    db.delete(server)
    db.commit()


@router.post("/{server_id}/test", response_model=ServerTestResult)
def test_server(server_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serveur introuvable.")

    secret = decrypt_secret(server.secret_encrypted)
    reachable, message, docker_version = ssh.test_connection(server.hostname, server.ssh_port, server.ssh_user, server.auth_method.value, secret)

    server.status = ServerStatus.reachable if reachable else ServerStatus.unreachable
    server.docker_version = docker_version
    server.last_checked_at = datetime.now(timezone.utc)
    db.commit()

    return ServerTestResult(reachable=reachable, message=message, docker_version=docker_version)
