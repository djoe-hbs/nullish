"""Request authentication: the session cookie -> the signed-in user."""

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
        .where(UserSession.token_hash == token_hash(token),
               UserSession.revoked_at.is_(None),
               UserSession.expires_at > datetime.now(UTC))
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has ended. Please sign in.")
    user, session = row
    request.state.session_id = session.id
    return user
