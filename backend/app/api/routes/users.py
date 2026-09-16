import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import get_settings
from app.core.security import generate_reset_token, hash_password
from app.db.session import get_db
from app.models.user import User, UserStatus
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services.email import send_invitation_email

router = APIRouter(prefix="/api/users", tags=["users"])
logger = logging.getLogger("app.users")
settings = get_settings()


@router.get("/", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.created_at.asc()).all()


@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    existing = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un compte existe déjà avec cet email.")

    if payload.method == "password" and not payload.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Un mot de passe est requis pour cette méthode.")

    placeholder_password = payload.password if payload.method == "password" else secrets.token_urlsafe(24)

    user = User(
        name=payload.name.strip(),
        email=payload.email.lower(),
        password_hash=hash_password(placeholder_password),
        role=payload.role,
        status=UserStatus.active if payload.method == "password" else UserStatus.invited,
    )

    if payload.method == "invite":
        raw_token, token_hash, expires_at = generate_reset_token()
        user.reset_token_hash = token_hash
        user.reset_token_expires_at = expires_at

    db.add(user)
    db.commit()
    db.refresh(user)

    if payload.method == "invite":
        set_password_link = f"{settings.frontend_url}/reset-password?token={raw_token}"
        try:
            send_invitation_email(to=user.email, name=user.name, set_password_link=set_password_link)
        except Exception:
            logger.exception("Failed to send invitation email to %s", user.email)

    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")

    data = payload.model_dump(exclude_unset=True)
    if "email" in data:
        data["email"] = data["email"].lower()
    for field, value in data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    if user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vous ne pouvez pas supprimer votre propre compte.")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")

    db.delete(user)
    db.commit()
