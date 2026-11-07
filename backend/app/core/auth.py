"""Login, sessions and role checks.

The session is a signed JWT in an httpOnly cookie, so page scripts can't read it.
The frontend only checks that the cookie exists (to redirect to /login); every API
call checks the token and the role here.
"""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

COOKIE_NAME = "healthops_session"
ROLES = ("intake", "biller", "admin")
PBKDF2_ROUNDS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(candidate.hex(), digest)


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(hours=settings.session_hours)}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not signed in")
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired, please sign in again")
    user = db.get(User, int(claims["sub"]))
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


def require_role(*roles: str):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles and user.role != "admin":
            raise HTTPException(403, f"This needs the {' or '.join(roles)} role")
        return user
    return check
