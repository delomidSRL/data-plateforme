from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.deps_medallion import get_owned_folder
from app.db.session import get_db
from app.models.medallion import MedallionFolder, MedallionProject
from app.models.user import User
from app.schemas.medallion import FolderCreate, FolderOut, FolderUpdate

router = APIRouter(prefix="/api/medallion/folders", tags=["medallion-folders"])

_DUPLICATE_NAME_DETAIL = "Un dossier porte déjà ce nom."


@router.get("/", response_model=list[FolderOut])
def list_folders(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    counts = dict(
        db.query(MedallionProject.folder_id, func.count(MedallionProject.id))
        .filter(MedallionProject.owner_id == current_user.id, MedallionProject.folder_id.isnot(None))
        .group_by(MedallionProject.folder_id)
        .all()
    )
    folders = db.query(MedallionFolder).filter(MedallionFolder.owner_id == current_user.id).order_by(MedallionFolder.name.asc()).all()
    return [FolderOut(id=f.id, name=f.name, project_count=counts.get(f.id, 0), created_at=f.created_at) for f in folders]


@router.post("/", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
def create_folder(payload: FolderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # owner_id is never accepted from the client — always the creator (Module 9's rule).
    folder = MedallionFolder(name=payload.name, owner_id=current_user.id)
    db.add(folder)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME_DETAIL)
    db.refresh(folder)
    return FolderOut(id=folder.id, name=folder.name, project_count=0, created_at=folder.created_at)


@router.patch("/{fid}", response_model=FolderOut)
def rename_folder(payload: FolderUpdate, folder: MedallionFolder = Depends(get_owned_folder), db: Session = Depends(get_db)):
    folder.name = payload.name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME_DETAIL)
    db.refresh(folder)
    project_count = db.query(MedallionProject).filter(MedallionProject.folder_id == folder.id).count()
    return FolderOut(id=folder.id, name=folder.name, project_count=project_count, created_at=folder.created_at)


@router.delete("/{fid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(folder: MedallionFolder = Depends(get_owned_folder), db: Session = Depends(get_db)):
    # Module 15 §3.2/D3 — deleting a folder never deletes a project: explicit reset here
    # (not just relying on the FK's ON DELETE SET NULL) so the in-session state is correct
    # immediately, and the intent is visible in the service layer, not just the schema.
    db.query(MedallionProject).filter(MedallionProject.folder_id == folder.id).update({"folder_id": None})
    db.delete(folder)
    db.commit()
    return None
