from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.dq_flag_registry import DqFlagRegistryEntry
from app.models.user import User
from app.schemas.dq_flag_registry import DqFlagRegistryEntryCreate, DqFlagRegistryEntryOut, DqFlagRegistryEntryUpdate

# UX ask — platform-wide, admin-managed (mirrors dbt_macros.py exactly): a data engineer only
# ever reads this list (their hand-written 04_annotated/05_validated/05_quarantine SQL can
# `{{ ref('dq_flag_registry') }}` it), only an admin can create/edit/delete an entry.
router = APIRouter(prefix="/api/dq-flag-registry", tags=["dq-flag-registry"])

_DUPLICATE_NAME_DETAIL = "Un flag porte déjà ce nom."


def _get_entry(db: Session, eid: int) -> DqFlagRegistryEntry:
    entry = db.get(DqFlagRegistryEntry, eid)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flag introuvable.")
    return entry


@router.get("/", response_model=list[DqFlagRegistryEntryOut])
def list_entries(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(DqFlagRegistryEntry).order_by(DqFlagRegistryEntry.flag_name.asc()).all()


@router.post("/", response_model=DqFlagRegistryEntryOut, status_code=status.HTTP_201_CREATED)
def create_entry(payload: DqFlagRegistryEntryCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    entry = DqFlagRegistryEntry(
        flag_name=payload.flag_name, category=payload.category, source_rule=payload.source_rule,
        issue_type=payload.issue_type, updated_by=current_user.id,
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME_DETAIL)
    db.refresh(entry)
    return entry


@router.put("/{eid}", response_model=DqFlagRegistryEntryOut)
def update_entry(eid: int, payload: DqFlagRegistryEntryUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    entry = _get_entry(db, eid)
    entry.flag_name = payload.flag_name
    entry.category = payload.category
    entry.source_rule = payload.source_rule
    entry.issue_type = payload.issue_type
    entry.updated_by = current_user.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME_DETAIL)
    db.refresh(entry)
    return entry


@router.delete("/{eid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(eid: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    entry = _get_entry(db, eid)
    db.delete(entry)
    db.commit()
    return None
