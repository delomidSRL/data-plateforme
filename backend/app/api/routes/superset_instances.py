from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.superset_instance import SupersetInstance
from app.models.user import User
from app.schemas.superset_instance import SupersetInstanceOut, SupersetInstanceTestResult
from app.services.superset_instances import get_superset_config, test_instance_connection

router = APIRouter(prefix="/api/superset-instances", tags=["superset-instances"])


def _get_instance(db: Session, iid: int) -> SupersetInstance:
    instance = db.get(SupersetInstance, iid)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instance Superset introuvable.")
    return instance


@router.get("/", response_model=list[SupersetInstanceOut])
def list_instances(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(SupersetInstance).order_by(SupersetInstance.created_at.asc()).all()


@router.get("/{iid}", response_model=SupersetInstanceOut)
def get_instance(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _get_instance(db, iid)


@router.post("/{iid}/test", response_model=SupersetInstanceTestResult)
async def test_instance(iid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    instance = _get_instance(db, iid)
    config = get_superset_config(instance)
    new_status, message, version = await test_instance_connection(config)

    instance.status = new_status
    instance.last_checked_at = datetime.now(timezone.utc)
    if version:
        instance.superset_version = version
    db.commit()

    return SupersetInstanceTestResult(reachable=new_status.value == "reachable", message=message, superset_version=version, status=new_status)
