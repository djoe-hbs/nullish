"""Sign in / sign out / current user / change password."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import COOKIE_NAME, current_user
from app.auth.security import (
    hash_password,
    needs_rehash,
    new_session_token,
    password_problem,
    token_hash,
    verify_password,
)
from app.config import Settings, get_settings
from app.db.models import User, UserSession
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

DB = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]

MAX_FAILED = 5
LOCKOUT = timedelta(minutes=15)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    role: str

    model_config = {"from_attributes": True}


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def set_cookie(response: Response, token: str, s: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=s.session_days * 86400,
        httponly=True,  # not readable by page scripts
        secure=s.cookie_secure,  # HTTPS-only in production
        samesite="lax",  # not sent on cross-site POSTs
        path="/",
    )


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, request: Request, response: Response, db: DB):
    s = get_settings()
    now = datetime.now(UTC)
    email = body.email.strip().casefold()
    user = await db.scalar(select(User).where(func.lower(User.email) == email))

    if user is not None and user.locked_until and user.locked_until > now:
        minutes = max(1, round((user.locked_until - now).total_seconds() / 60))
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Too many wrong attempts. Try again in {minutes} min.")

    if not verify_password(user.password_hash if user else None, body.password):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                user.locked_until = now + LOCKOUT
                user.failed_logins = 0
            await db.commit()
        await asyncio.sleep(0.4)  # slows down guessing a little more
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.")

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token = new_session_token()
    db.add(UserSession(
        user_id=user.id, token_hash=token_hash(token),
        user_agent=(request.headers.get("user-agent") or "")[:500],
        ip=request.client.host if request.client else None,
        expires_at=now + timedelta(days=s.session_days),
    ))
    await db.commit()
    set_cookie(response, token, s)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DB):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await db.execute(update(UserSession)
                         .where(UserSession.token_hash == token_hash(token))
                         .values(revoked_at=datetime.now(UTC)))
        await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: ChangePasswordIn, request: Request, user: CurrentUser, db: DB):
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is wrong.")
    if problem := password_problem(body.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "The new password must be different from the current one.")
    db_user = await db.get(User, user.id)
    db_user.password_hash = hash_password(body.new_password)
    # Sign out everywhere else; this browser stays signed in.
    await db.execute(update(UserSession)
                     .where(UserSession.user_id == user.id,
                            UserSession.id != request.state.session_id,
                            UserSession.revoked_at.is_(None))
                     .values(revoked_at=datetime.now(UTC)))
    await db.commit()


async def ensure_admin(db: AsyncSession, s: Settings) -> bool:
    """First start only: create the admin from ADMIN_EMAIL / ADMIN_PASSWORD if there are no users.
    Never overwrites an existing account (change the password in the app afterwards)."""
    if await db.scalar(select(func.count()).select_from(User)):
        return False
    if not s.admin_email or not s.admin_password:
        return False
    db.add(User(email=s.admin_email.strip().casefold(),
                password_hash=hash_password(s.admin_password), role="admin"))
    await db.commit()
    return True
