"""Sign in (email or username) / sign out / profile / sessions / password change."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import COOKIE_NAME, current_user
from app.auth.security import (
    hash_password,
    needs_rehash,
    new_session_token,
    normalize_email,
    password_problem,
    token_hash,
    username_problem,
    verify_password,
)
from app.config import Settings, get_settings
from app.db.models import AuthToken, Conversation, DataSource, User, UserSession
from app.db.session import get_session
from app.email import EmailDeliveryError, Mailer, get_mailer

router = APIRouter(prefix="/auth", tags=["auth"])

DB = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(current_user)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]

MAX_FAILED = 5
LOCKOUT = timedelta(minutes=15)


class LoginIn(BaseModel):
    identifier: str = Field(
        min_length=1,
        max_length=320,
        validation_alias=AliasChoices("identifier", "email", "username"),
    )
    password: str = Field(min_length=1, max_length=256)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class RegisterOut(BaseModel):
    id: int
    email: str
    username: str
    name: str
    pending_approval: bool = True
    email_verification_required: bool = True
    email_sent: bool = True
    message: str


class MessageOut(BaseModel):
    message: str


class IdentifierIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)


class VerifyEmailIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    username: str | None = None
    name: str = ""
    role: str
    is_active: bool = True
    approved_at: datetime | None = None
    email_verified_at: datetime | None = None
    storage_mode: Literal["db", "local"] | None = None
    last_login_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class StorageModeIn(BaseModel):
    storage_mode: Literal["db", "local"]
    purge_server_data: bool = False


class ProfilePatchIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    username: str | None = Field(None, min_length=3, max_length=64)
    email: str | None = Field(None, min_length=3, max_length=320)


class SessionOut(BaseModel):
    id: int
    user_agent: str | None
    ip: str | None
    created_at: datetime
    expires_at: datetime
    is_current: bool


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


async def _find_user_by_identifier(db: AsyncSession, identifier: str) -> User | None:
    ident = identifier.strip().casefold()
    if not ident:
        return None
    return await db.scalar(
        select(User).where(
            or_(
                func.lower(User.email) == ident,
                User.username.is_not(None) & (func.lower(User.username) == ident),
            )
        )
    )


async def _issue_auth_token(
    db: AsyncSession, *, user_id: int, purpose: str, expires_delta: timedelta
) -> str:
    now = datetime.now(UTC)
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    raw = new_session_token()
    db.add(
        AuthToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash(raw),
            expires_at=now + expires_delta,
        )
    )
    return raw


@router.post("/register", response_model=RegisterOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, db: DB, mailer: MailerDep):
    """Self-registration for users. Requires email verification and administrator approval."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name cannot be empty.")
    uname = body.username.strip()
    if prob := username_problem(uname):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)
    email = normalize_email(body.email)
    if email is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid email address.")
    if prob := password_problem(body.password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)

    s = get_settings()
    user = User(
        email=email,
        username=uname.lower(),
        name=name,
        password_hash=hash_password(body.password),
        role="member",
        is_active=False,
        approved_at=None,
        email_verified_at=None,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with that email or username already exists."
        ) from exc

    raw_token = await _issue_auth_token(
        db,
        user_id=user.id,
        purpose="verify_email",
        expires_delta=timedelta(hours=s.email_verify_token_expire_hours),
    )
    await db.commit()
    await db.refresh(user)

    email_sent = False
    if mailer.is_configured:
        try:
            await mailer.send_verification_email(
                to_email=user.email,
                name=user.name,
                username=user.username,
                token=raw_token,
            )
            email_sent = True
        except EmailDeliveryError:
            email_sent = False

    msg = (
        "Account created. Check your inbox to verify your email address, then wait for an "
        "administrator to approve your account."
        if email_sent
        else "Account created. Use 'Resend verification email' once email delivery is ready, "
        "or ask an administrator to verify your account."
    )
    return RegisterOut(
        id=user.id,
        email=user.email,
        username=user.username or uname.lower(),
        name=user.name,
        pending_approval=True,
        email_verification_required=True,
        email_sent=email_sent,
        message=msg,
    )


@router.post("/verify-email", response_model=MessageOut)
async def verify_email(body: VerifyEmailIn, db: DB):
    now = datetime.now(UTC)
    row = await db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash(body.token.strip()),
            AuthToken.purpose == "verify_email",
        )
    )
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This verification link is invalid or has expired. Request a new one.",
        )
    user = await db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account not found.")
    row.used_at = now
    if user.email_verified_at is None:
        user.email_verified_at = now
    await db.commit()
    if user.is_active:
        return MessageOut(message="Email address verified! You can sign in now.")
    return MessageOut(
        message="Email address verified! Your account is now waiting for administrator approval."
    )


@router.post("/resend-verification", response_model=MessageOut)
async def resend_verification(body: IdentifierIn, db: DB, mailer: MailerDep):
    if not mailer.is_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Email delivery is not configured on this server.",
        )
    s = get_settings()
    user = await _find_user_by_identifier(db, body.identifier)
    if user is not None and user.email_verified_at is None:
        raw_token = await _issue_auth_token(
            db,
            user_id=user.id,
            purpose="verify_email",
            expires_delta=timedelta(hours=s.email_verify_token_expire_hours),
        )
        await db.commit()
        try:
            await mailer.send_verification_email(
                to_email=user.email,
                name=user.name,
                username=user.username,
                token=raw_token,
            )
        except EmailDeliveryError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return MessageOut(
        message="If an unverified account matches that email or username, "
        "a new verification link has been sent."
    )


@router.post("/forgot-password", response_model=MessageOut)
async def forgot_password(body: IdentifierIn, db: DB, mailer: MailerDep):
    if not mailer.is_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Email delivery is not configured on this server.",
        )
    s = get_settings()
    user = await _find_user_by_identifier(db, body.identifier)
    # Send reset links for active or pending accounts; skip explicitly deactivated ones.
    if user is not None and (user.is_active or user.approved_at is None):
        raw_token = await _issue_auth_token(
            db,
            user_id=user.id,
            purpose="reset_password",
            expires_delta=timedelta(minutes=s.password_reset_token_expire_min),
        )
        await db.commit()
        try:
            await mailer.send_password_reset_email(
                to_email=user.email,
                name=user.name,
                username=user.username,
                token=raw_token,
            )
        except EmailDeliveryError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return MessageOut(
        message="If an account matches that email or username, a password reset link "
        "and sign-in ID have been sent."
    )


@router.post("/reset-password", response_model=MessageOut)
async def reset_password(body: ResetPasswordIn, db: DB):
    if prob := password_problem(body.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)
    now = datetime.now(UTC)
    row = await db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash(body.token.strip()),
            AuthToken.purpose == "reset_password",
        )
    )
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This password reset link is invalid or has expired. Request a new one.",
        )
    user = await db.get(User, row.user_id)
    if user is None or (not user.is_active and user.approved_at is not None):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your account has been deactivated. Contact an administrator.",
        )

    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == "reset_password",
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    row.used_at = now
    user.password_hash = hash_password(body.new_password)
    user.failed_logins = 0
    user.locked_until = None
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()
    return MessageOut(
        message="Your password has been reset. You can sign in with your new password now."
    )


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, request: Request, response: Response, db: DB):
    s = get_settings()
    now = datetime.now(UTC)
    user = await _find_user_by_identifier(db, body.identifier)

    if user is not None and user.locked_until and user.locked_until > now:
        minutes = max(1, round((user.locked_until - now).total_seconds() / 60))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many wrong attempts. Try again in {minutes} min.",
        )

    if not verify_password(user.password_hash if user else None, body.password):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                user.locked_until = now + LOCKOUT
                user.failed_logins = 0
            await db.commit()
        await asyncio.sleep(0.4)  # slows down guessing a little more
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email/username or password.")

    if user.email_verified_at is None:
        if not user.is_active and user.approved_at is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Please verify your email address before signing in. "
                "Your account also requires administrator approval.",
            )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Please verify your email address before signing in.",
        )

    if not user.is_active:
        if user.approved_at is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Your account is pending administrator approval.",
            )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your account has been deactivated. Contact an administrator.",
        )

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


@router.post("/storage-mode", response_model=UserOut)
@router.patch("/storage-mode", response_model=UserOut)
async def set_storage_mode(body: StorageModeIn, user: CurrentUser, db: DB):
    db_user = await db.get(User, user.id)
    db_user.storage_mode = body.storage_mode
    if body.storage_mode == "local" and body.purge_server_data:
        await db.execute(delete(Conversation).where(Conversation.user_id == user.id))
        await db.execute(delete(DataSource).where(DataSource.user_id == user.id))
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.patch("/profile", response_model=UserOut)
async def update_profile(body: ProfilePatchIn, user: CurrentUser, db: DB):
    db_user = await db.get(User, user.id)
    fields = body.model_dump(exclude_unset=True)
    if user.role != "admin":
        req_user = fields.get("username")
        if req_user is not None and req_user.strip().lower() != (user.username or ""):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Users cannot change their username. Contact an administrator.",
            )
        req_email = fields.get("email")
        if req_email is not None:
            norm_req_email = normalize_email(req_email)
            if norm_req_email is None or norm_req_email != user.email.lower():
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Users cannot change their email address. Contact an administrator.",
                )
    if "name" in fields and fields["name"] is not None:
        cleaned_name = fields["name"].strip()
        if not cleaned_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name cannot be empty.")
        db_user.name = cleaned_name
    if user.role == "admin":
        if "username" in fields and fields["username"] is not None:
            u = fields["username"].strip()
            if prob := username_problem(u):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)
            db_user.username = u.lower()
        if "email" in fields and fields["email"] is not None:
            em = normalize_email(fields["email"])
            if em is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid email address.")
            db_user.email = em
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email or username is already in use."
        ) from exc
    await db.refresh(db_user)
    return db_user


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(request: Request, user: CurrentUser, db: DB):
    now = datetime.now(UTC)
    rows = (
        await db.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.id.desc())
        )
    ).all()
    cur_id = getattr(request.state, "session_id", None)
    return [
        SessionOut(
            id=r.id,
            user_agent=r.user_agent,
            ip=r.ip,
            created_at=r.created_at,
            expires_at=r.expires_at,
            is_current=(r.id == cur_id),
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: int, request: Request, user: CurrentUser, db: DB):
    sess = await db.get(UserSession, session_id)
    if sess is None or sess.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    if sess.id == getattr(request.state, "session_id", None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Use Sign out to end your current session."
        )
    if sess.revoked_at is None:
        sess.revoked_at = datetime.now(UTC)
        await db.commit()


@router.post("/sessions/revoke-others", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_sessions(request: Request, user: CurrentUser, db: DB):
    cur_id = getattr(request.state, "session_id", None)
    stmt = update(UserSession).where(
        UserSession.user_id == user.id,
        UserSession.revoked_at.is_(None),
    )
    if cur_id is not None:
        stmt = stmt.where(UserSession.id != cur_id)
    await db.execute(stmt.values(revoked_at=datetime.now(UTC)))
    await db.commit()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: ChangePasswordIn, request: Request, user: CurrentUser, db: DB):
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is wrong.")
    if problem := password_problem(body.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    if body.new_password == body.current_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The new password must be different from the current one.",
        )
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
    email = s.admin_email.strip().casefold()
    local = re.sub(r"[^a-z0-9._-]+", "_", email.split("@", 1)[0]).strip("._-") or "admin"
    username = local[:64] if len(local) >= 3 else "admin"
    db.add(User(
        email=email,
        username=username,
        name="Administrator",
        password_hash=hash_password(s.admin_password),
        role="admin",
        is_active=True,
        approved_at=datetime.now(UTC),
        email_verified_at=datetime.now(UTC),
    ))
    await db.commit()
    return True
