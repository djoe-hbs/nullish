"""Request authentication: the session cookie -> the signed-in user and role checks."""

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import token_hash
from app.db.models import User, UserSession
from app.db.session import get_session

COOKIE_NAME = "nullish_session"


async def current_user(request: Request, db: AsyncSession = Depends(get_session)) -> User:  # noqa: B008
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    row = (await db.execute(
        select(User, UserSession)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.token_hash == token_hash(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has ended. Please sign in.")
    user, session = row
    if user.email_verified_at is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Please verify your email address before signing in.",
        )
    if not user.is_active:
        msg = (
            "Your account is pending administrator approval."
            if user.approved_at is None
            else "Your account has been deactivated. Contact an administrator."
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, msg)
    request.state.session_id = session.id
    return user


async def require_admin(user: User = Depends(current_user)) -> User:  # noqa: B008
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access is required.")
    return user
