from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.data_source import DataSource
from app.models.infra_stack import InfraStack, StackStatus
from app.models.server import Server
from app.models.user import User
from app.schemas.stack import StackCreate, StackDeployReport, StackOut, StackPreview, StackVerifyReport
from app.services import deploy
from app.services.compose import render_compose
from app.services.stack_secrets import apply_defaults, encrypt_services, mask_services

router = APIRouter(prefix="/api/servers/{server_id}/stacks", tags=["stacks"])


def _get_server(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serveur introuvable.")
    return server


def _get_stack(db: Session, server_id: int, sid: int) -> InfraStack:
    stack = db.query(InfraStack).filter(InfraStack.id == sid, InfraStack.server_id == server_id).first()
    if stack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stack introuvable.")
    return stack


def _to_out(stack: InfraStack) -> StackOut:
    out = StackOut.model_validate(stack)
    out.services = mask_services(out.services)
    return out


@router.get("/", response_model=list[StackOut])
def list_stacks(server_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _get_server(db, server_id)
    stacks = db.query(InfraStack).filter(InfraStack.server_id == server_id).order_by(InfraStack.created_at.asc()).all()
    return [_to_out(s) for s in stacks]


@router.post("/", response_model=StackOut, status_code=status.HTTP_201_CREATED)
def create_stack(server_id: int, payload: StackCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _get_server(db, server_id)

    services_plain = payload.services.model_dump()
    services_plain = apply_defaults(services_plain)
    services_encrypted = encrypt_services(services_plain)

    stack = InfraStack(server_id=server_id, name=payload.name, services=services_encrypted, status=StackStatus.draft)
    db.add(stack)
    db.commit()
    db.refresh(stack)
    return _to_out(stack)


@router.put("/{sid}", response_model=StackOut)
def update_stack(server_id: int, sid: int, payload: StackCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)

    from app.services.stack_secrets import decrypt_services, merge_with_existing

    existing_plain = decrypt_services(stack.services)
    incoming_plain = payload.services.model_dump()
    merged_plain = merge_with_existing(existing_plain, incoming_plain)
    merged_plain = apply_defaults(merged_plain)

    stack.name = payload.name
    stack.services = encrypt_services(merged_plain)
    db.commit()
    db.refresh(stack)
    return _to_out(stack)


@router.get("/{sid}/preview", response_model=StackPreview)
def preview_stack(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)

    warnings = []
    if (stack.services.get("airflow") or {}).get("enabled"):
        warnings.append("Airflow nécessite au moins 4 Go de RAM et 2 CPU sur le serveur cible.")

    try:
        masked_yaml = render_compose(stack, masked=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Configuration invalide : {exc}")

    return StackPreview(compose_yaml=masked_yaml, warnings=warnings)


@router.post("/{sid}/deploy", response_model=StackDeployReport)
def deploy_stack_endpoint(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)

    # backfill any auto-generated secrets added to the schema after this stack was first created
    from app.services.stack_secrets import decrypt_services as _decrypt

    plain = apply_defaults(_decrypt(stack.services))
    stack.services = encrypt_services(plain)

    stack.status = StackStatus.deploying
    db.commit()

    try:
        report = deploy.deploy_stack(db, stack, server)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return StackDeployReport(compose_written=True, containers=report["containers"], registered_sources=report["registered_sources"])


@router.post("/{sid}/stop", status_code=status.HTTP_204_NO_CONTENT)
def stop_stack_endpoint(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)
    try:
        deploy.stop_stack(server, stack)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    stack.status = StackStatus.stopped
    db.commit()


@router.post("/{sid}/restart", status_code=status.HTTP_204_NO_CONTENT)
def restart_stack_endpoint(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)
    try:
        deploy.restart_stack(server, stack)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    stack.status = StackStatus.running
    db.commit()


@router.get("/{sid}/status")
def stack_status_endpoint(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)
    try:
        containers = deploy.get_status(server, stack)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return [c.__dict__ for c in containers]


@router.post("/{sid}/verify", response_model=StackVerifyReport)
def verify_stack_endpoint(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)
    try:
        report = deploy.verify_stack(db, stack, server)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return StackVerifyReport(**report)


@router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stack_endpoint(server_id: int, sid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    server = _get_server(db, server_id)
    stack = _get_stack(db, server_id, sid)

    if stack.status in (StackStatus.running, StackStatus.stopped):
        try:
            deploy.teardown_stack(db, server, stack)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    else:
        db.query(DataSource).filter(DataSource.stack_id == stack.id).delete()

    db.delete(stack)
    db.commit()
