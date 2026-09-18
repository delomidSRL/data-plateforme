from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.dbt_macro import DbtMacro
from app.models.user import User
from app.schemas.dbt_macro import DbtMacroCreate, DbtMacroOut, DbtMacroUpdate
from app.services.dbt_macros import BUILTIN_MACRO_NAMES, extract_macro_name

# UX ask — platform-wide, admin-managed (mirrors ml_templates.py's MLTemplate library): a data
# engineer only ever reads this list (offered in every project's 03_standardized SQL editor,
# DatasetPanel.jsx), only an admin can create/edit/delete a macro (require_admin).
router = APIRouter(prefix="/api/dbt-macros", tags=["dbt-macros"])

_DUPLICATE_NAME_DETAIL = "Une macro porte déjà ce nom."


def _reject_builtin_name(name: str) -> None:
    if name in BUILTIN_MACRO_NAMES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"« {name} » est déjà le nom d'une macro intégrée à la plateforme — choisissez un autre nom.")


def _check_definition_matches_name(name: str, definition: str) -> None:
    """The admin writes the whole `{% macro %}` block by hand — this is the one thing that
    genuinely can't be inferred, so it's checked explicitly (a plain HTTPException, not a
    pydantic schema validator, so the frontend gets one clean message instead of pydantic's
    list-of-errors 422 shape — see schemas.dbt_macro.DbtMacroBase's own docstring)."""
    declared = extract_macro_name(definition)
    if declared is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le corps doit contenir un bloc « {% macro nom(...) %} ... {% endmacro %} » valide.")
    if declared != name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Le nom dans « {{% macro {declared}(...) %}} » ne correspond pas au nom « {name} » saisi ci-dessus.")


def _get_macro(db: Session, mid: int) -> DbtMacro:
    macro = db.get(DbtMacro, mid)
    if macro is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Macro introuvable.")
    return macro


@router.get("/", response_model=list[DbtMacroOut])
def list_macros(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(DbtMacro).order_by(DbtMacro.name.asc()).all()


@router.post("/", response_model=DbtMacroOut, status_code=status.HTTP_201_CREATED)
def create_macro(payload: DbtMacroCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    _reject_builtin_name(payload.name)
    _check_definition_matches_name(payload.name, payload.definition)
    macro = DbtMacro(
        name=payload.name, description=payload.description, definition=payload.definition,
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
def update_macro(mid: int, payload: DbtMacroUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    macro = _get_macro(db, mid)
    if payload.name != macro.name:
        _reject_builtin_name(payload.name)
    _check_definition_matches_name(payload.name, payload.definition)
    macro.name = payload.name
    macro.description = payload.description
    macro.definition = payload.definition
    macro.updated_by = current_user.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME_DETAIL)
    db.refresh(macro)
    return macro


@router.delete("/{mid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_macro(mid: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    macro = _get_macro(db, mid)
    db.delete(macro)
    db.commit()
    return None
