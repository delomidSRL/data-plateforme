"""Seed the initial admin account. Idempotent: safe to re-run."""

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User, UserRole, UserStatus

settings = get_settings()


def main():
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == settings.admin_email.lower()).first()
        if existing is not None:
            print(f"Admin account already exists: {existing.email}")
            return

        admin = User(
            name=settings.admin_name,
            email=settings.admin_email.lower(),
            password_hash=hash_password(settings.admin_password),
            role=UserRole.admin,
            status=UserStatus.active,
        )
        db.add(admin)
        db.commit()
        print(f"Admin account created: {admin.email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
