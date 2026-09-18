from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.deps_medallion import get_owned_project, get_readable_project
from app.db.session import get_db
from app.models.dbt_macro import DbtMacro
from app.models.medallion import MedallionProject
from app.models.user import User
from app.schemas.dbt_macro import DbtMacroCreate, DbtMacroOut, DbtMacroUpdate
from app.services.dbt_macros import BUILTIN_MACRO_NAMES

router = APIRouter(prefix="/api/medallion/projects/{pid}/macros", tags=["medallion-macros"])

_DUPLICATE_NAME_DETAIL = "Une macro de ce projet porte déjà ce nom."


def _reject_builtin_name(name: str) -> None:
    if name in BUILTIN_MACRO_NAMES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"« {name} » est déjà le nom d'une macro intégrée à la plateforme — choisissez un autre nom.")


def _get_owned_macro(pid: int, mid: int, db: Session, project: MedallionProject) -> DbtMacro:
    macro = db.query(DbtMacro).filter(DbtMacro.id == mid, DbtMacro.project_id == project.id).first()
    if macro is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Macro introuvable.")
    return macro


@router.get("/", response_model=list[DbtMacroOut])
def list_macros(pid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_readable_project)):
    return db.query(DbtMacro).filter(DbtMacro.project_id == project.id).order_by(DbtMacro.name.asc()).all()


@router.post("/", response_model=DbtMacroOut, status_code=status.HTTP_201_CREATED)
def create_macro(
    pid: int, payload: DbtMacroCreate, db: Session = Depends(get_db),
    project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user),
):
    _reject_builtin_name(payload.name)
    macro = DbtMacro(
        project_id=project.id, name=payload.name, description=payload.description,
        parameters=[p.model_dump() for p in payload.parameters], sql_body=payload.sql_body,
        updated_by=current_user.id,
    )
    db.add(macro)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME_DETAIL)
    db.refresh(macro)
    return macro


@router.put("/{mid}", response_model=DbtMacroOut)
def update_macro(
    pid: int, mid: int, payload: DbtMacroUpdate, db: Session = Depends(get_db),
    project: MedallionProject = Depends(get_owned_project), current_user: User = Depends(get_current_user),
):
    macro = _get_owned_macro(pid, mid, db, project)
    if payload.name != macro.name:
        _reject_builtin_name(payload.name)
    macro.name = payload.name
    macro.description = payload.description
    macro.parameters = [p.model_dump() for p in payload.parameters]
    macro.sql_body = payload.sql_body
    macro.updated_by = current_user.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME_DETAIL)
    db.refresh(macro)
    return macro


@router.delete("/{mid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_macro(pid: int, mid: int, db: Session = Depends(get_db), project: MedallionProject = Depends(get_owned_project)):
    macro = _get_owned_macro(pid, mid, db, project)
    db.delete(macro)
    db.commit()
    return None
