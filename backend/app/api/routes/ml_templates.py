from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.medallion import MLTemplate
from app.models.user import User
from app.schemas.ml_template import MLTemplateCreate, MLTemplateOut, MLTemplateUpdate

router = APIRouter(prefix="/api/ml-templates", tags=["ml-templates"])


def _get_template(db: Session, tid: int) -> MLTemplate:
    template = db.get(MLTemplate, tid)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template introuvable.")
    return template


@router.get("/", response_model=list[MLTemplateOut])
def list_templates(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(MLTemplate).order_by(MLTemplate.ml_objective.asc(), MLTemplate.name.asc()).all()


@router.get("/{tid}", response_model=MLTemplateOut)
def get_template(tid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _get_template(db, tid)


@router.post("/", response_model=MLTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(payload: MLTemplateCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    template = MLTemplate(**payload.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.put("/{tid}", response_model=MLTemplateOut)
def update_template(tid: int, payload: MLTemplateUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    template = _get_template(db, tid)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    db.commit()
    db.refresh(template)
    return template


@router.delete("/{tid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(tid: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    template = _get_template(db, tid)
    db.delete(template)
    db.commit()
