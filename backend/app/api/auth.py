from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import user_json
from app.config import settings
from app.core import audit
from app.core.auth import COOKIE_NAME, check_password, create_token, current_user
from app.db import get_db
from app.models import User
from app.seed import DEMO_USERS

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class DemoLoginRequest(BaseModel):
    email: str


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not check_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password")
    return _start_session(user, response, db)


@router.post("/demo-login")
def demo_login(body: DemoLoginRequest, response: Response, db: Session = Depends(get_db)):
    """One-click sign-in as a demo role. Only exists while demo data is on."""
    if not settings.seed_demo_data or body.email not in {email for email, _, _ in DEMO_USERS}:
        raise HTTPException(404, "Demo sign-in is turned off")
    user = db.scalar(select(User).where(User.email == body.email))
    if not user:
        raise HTTPException(404, "Demo user not found")
    return _start_session(user, response, db)


def _start_session(user: User, response: Response, db: Session) -> dict:
    response.set_cookie(COOKIE_NAME, create_token(user), httponly=True, samesite="lax",
                        secure=not settings.database_url.startswith("sqlite"),  # https in production
                        max_age=settings.session_hours * 3600)
    audit.record(db, user.email, "auth.login", "user", user.id)
    return user_json(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_json(user)


@router.get("/demo-accounts")
def demo_accounts():
    """Shown on the login page when demo data is on."""
    if not settings.seed_demo_data:
        return []
    return [{"email": email, "name": name, "role": role} for email, name, role in DEMO_USERS]
