"""Admin-only endpoints: user management, aggregate system metrics, and runtime settings.

Privacy & security boundaries:
- Admins can see each user's source inventory (website names/URLs & counts; database display
  names, kind, and edit policy) for oversight, but NEVER another user's chats/messages,
  database hosts/usernames/passwords/logs, or crawled content.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.auth.security import (
    hash_password,
    normalize_email,
    password_problem,
    username_problem,
)
from app.config import get_settings
from app.db.models import (
    AuthToken,
    Chunk,
    Conversation,
    CrawlJob,
    DataSource,
    DsPendingChange,
    DsQueryLog,
    DsSchemaItem,
    Message,
    Page,
    Site,
    User,
    UserSession,
)
from app.db.session import get_session
from app.legal_store import (
    LegalSettingsData,
    get_legal_settings,
    reset_legal_settings,
    save_legal_settings,
)
from app.settings_store import SystemSettingsData, get_system_settings, save_system_settings

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_session)]
AdminUser = Annotated[User, Depends(require_admin)]


# ---- schemas -------------------------------------------------------------------------------


class AdminSiteSummary(BaseModel):
    id: int
    name: str
    root_url: str
    enabled: bool
    pages: int = 0
    chunks: int = 0
    last_job_status: str | None = None


class AdminDbSummary(BaseModel):
    """Non-credential database summary shown to admins (never host/user/password/logs)."""

    id: int
    name: str
    kind: str
    edit_policy: str
    enabled: bool
    tables: int = 0
    last_test_ok: bool | None = None
    schema_refreshed_at: datetime | None = None


class AdminUserOut(BaseModel):
    id: int
    email: str
    username: str | None
    name: str
    role: str
    is_active: bool
    pending_approval: bool = False
    approved_at: datetime | None = None
    email_verified: bool = False
    email_verified_at: datetime | None = None
    storage_mode: str | None = None
    last_login_at: datetime | None
    locked_until: datetime | None
    created_at: datetime
    conversation_count: int = 0
    sites: list[AdminSiteSummary] = []
    databases: list[AdminDbSummary] = []


class AdminUserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    username: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)
    role: Literal["admin", "member"] = "member"


class AdminUserUpdate(BaseModel):
    email: str | None = Field(None, min_length=3, max_length=320)
    username: str | None = Field(None, min_length=3, max_length=64)
    name: str | None = Field(None, min_length=1, max_length=120)
    role: Literal["admin", "member"] | None = None
    is_active: bool | None = None
    email_verified: bool | None = None
    password: str | None = Field(None, min_length=1, max_length=256)
    unlock: bool | None = None


class AdminVerifyEmailIn(BaseModel):
    approve: bool = True


# ---- helpers -------------------------------------------------------------------------------


async def _user_summaries(db: AsyncSession, users: list[User]) -> list[AdminUserOut]:
    if not users:
        return []
    uids = [u.id for u in users]

    conv_counts = dict(
        (
            await db.execute(
                select(Conversation.user_id, func.count())
                .where(Conversation.user_id.in_(uids))
                .group_by(Conversation.user_id)
            )
        ).all()
    )

    sites = (
        await db.scalars(select(Site).where(Site.user_id.in_(uids)).order_by(Site.id))
    ).all()
    site_ids = [s.id for s in sites]
    page_counts: dict[int, int] = {}
    chunk_counts: dict[int, int] = {}
    last_job_status: dict[int, str] = {}
    if site_ids:
        page_counts = dict(
            (
                await db.execute(
                    select(Page.site_id, func.count())
                    .where(Page.site_id.in_(site_ids), Page.status == "active")
                    .group_by(Page.site_id)
                )
            ).all()
        )
        chunk_counts = dict(
            (
                await db.execute(
                    select(Page.site_id, func.count(Chunk.id))
                    .join(Page, Page.id == Chunk.page_id)
                    .where(
                        Page.site_id.in_(site_ids),
                        Page.status == "active",
                        Chunk.page_version == Page.version,
                    )
                    .group_by(Page.site_id)
                )
            ).all()
        )
        latest = (
            select(CrawlJob.site_id, func.max(CrawlJob.id).label("job_id"))
            .where(CrawlJob.site_id.in_(site_ids))
            .group_by(CrawlJob.site_id)
            .subquery()
        )
        for j in await db.scalars(select(CrawlJob).join(latest, CrawlJob.id == latest.c.job_id)):
            last_job_status[j.site_id] = j.status

    sites_by_user: dict[int, list[AdminSiteSummary]] = {uid: [] for uid in uids}
    for s in sites:
        if s.user_id is not None:
            sites_by_user[s.user_id].append(
                AdminSiteSummary(
                    id=s.id,
                    name=s.name,
                    root_url=s.root_url,
                    enabled=s.enabled,
                    pages=page_counts.get(s.id, 0),
                    chunks=chunk_counts.get(s.id, 0),
                    last_job_status=last_job_status.get(s.id),
                )
            )

    dbs = (
        await db.scalars(
            select(DataSource).where(DataSource.user_id.in_(uids)).order_by(DataSource.id)
        )
    ).all()
    db_ids = [d.id for d in dbs]
    table_counts: dict[int, int] = {}
    if db_ids:
        table_counts = dict(
            (
                await db.execute(
                    select(DsSchemaItem.source_id, func.count())
                    .where(
                        DsSchemaItem.source_id.in_(db_ids),
                        DsSchemaItem.column_name.is_(None),
                    )
                    .group_by(DsSchemaItem.source_id)
                )
            ).all()
        )

    dbs_by_user: dict[int, list[AdminDbSummary]] = {uid: [] for uid in uids}
    for d in dbs:
        if d.user_id is not None:
            dbs_by_user[d.user_id].append(
                AdminDbSummary(
                    id=d.id,
                    name=d.name,
                    kind=d.kind,
                    edit_policy=d.edit_policy,
                    enabled=d.enabled,
                    tables=table_counts.get(d.id, 0),
                    last_test_ok=d.last_test_ok,
                    schema_refreshed_at=d.schema_refreshed_at,
                )
            )

    return [
        AdminUserOut(
            id=u.id,
            email=u.email,
            username=u.username,
            name=u.name,
            role=u.role,
            is_active=u.is_active,
            pending_approval=(not u.is_active) and (u.approved_at is None),
            approved_at=u.approved_at,
            email_verified=u.email_verified_at is not None,
            email_verified_at=u.email_verified_at,
            storage_mode=u.storage_mode,
            last_login_at=u.last_login_at,
            locked_until=u.locked_until,
            created_at=u.created_at,
            conversation_count=conv_counts.get(u.id, 0),
            sites=sites_by_user.get(u.id, []),
            databases=dbs_by_user.get(u.id, []),
        )
        for u in users
    ]


async def _active_admin_count(db: AsyncSession) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        )
    ) or 0


# ---- user management -----------------------------------------------------------------------


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: DB) -> list[AdminUserOut]:
    users = list((await db.scalars(select(User).order_by(User.id))).all())
    return await _user_summaries(db, users)


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: AdminUserCreate, db: DB) -> AdminUserOut:
    email = normalize_email(body.email)
    if email is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid email address.")
    uname = body.username.strip()
    if prob := username_problem(uname):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name cannot be empty.")
    if prob := password_problem(body.password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)

    user = User(
        email=email,
        username=uname.lower(),
        name=name,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        approved_at=datetime.now(UTC),
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A user with that email or username already exists."
        ) from exc
    await db.refresh(user)
    return (await _user_summaries(db, [user]))[0]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: int, body: AdminUserUpdate, admin: AdminUser, db: DB
) -> AdminUserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    fields = body.model_dump(exclude_unset=True)
    if user.id == admin.id:
        if fields.get("is_active") is False:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account."
            )
        if fields.get("role") == "member":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "You cannot remove your own admin role."
            )
        if fields.get("email_verified") is False:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "You cannot unverify your own email."
            )

    removing_admin = (
        user.role == "admin"
        and user.is_active
        and (fields.get("role") == "member" or fields.get("is_active") is False)
    )
    if removing_admin and await _active_admin_count(db) <= 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "At least one active administrator is required."
        )

    revoke_sessions = False
    if "email" in fields and fields["email"] is not None:
        em = normalize_email(fields["email"])
        if em is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid email address.")
        user.email = em
    if "username" in fields and fields["username"] is not None:
        u = fields["username"].strip()
        if prob := username_problem(u):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)
        user.username = u.lower()
    if "name" in fields and fields["name"] is not None:
        n = fields["name"].strip()
        if not n:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name cannot be empty.")
        user.name = n
    if "role" in fields and fields["role"] is not None:
        user.role = fields["role"]
    if "is_active" in fields and fields["is_active"] is not None:
        user.is_active = fields["is_active"]
        if not user.is_active:
            revoke_sessions = True
        else:
            if user.approved_at is None:
                user.approved_at = datetime.now(UTC)
            user.failed_logins = 0
            user.locked_until = None
    if "password" in fields and fields["password"] is not None:
        if prob := password_problem(fields["password"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, prob)
        user.password_hash = hash_password(fields["password"])
        user.failed_logins = 0
        user.locked_until = None
        revoke_sessions = True
    if fields.get("unlock"):
        user.failed_logins = 0
        user.locked_until = None
    if "email_verified" in fields and fields["email_verified"] is not None:
        if fields["email_verified"]:
            now = datetime.now(UTC)
            if user.email_verified_at is None:
                user.email_verified_at = now
            await db.execute(
                update(AuthToken)
                .where(
                    AuthToken.user_id == user.id,
                    AuthToken.purpose == "verify_email",
                    AuthToken.used_at.is_(None),
                )
                .values(used_at=now)
            )
        else:
            user.email_verified_at = None
            revoke_sessions = True

    if revoke_sessions:
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That email or username is already in use."
        ) from exc
    await db.refresh(user)
    return (await _user_summaries(db, [user]))[0]


@router.post("/users/{user_id}/approve", response_model=AdminUserOut)
async def approve_user(user_id: int, db: DB) -> AdminUserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    user.is_active = True
    if user.approved_at is None:
        user.approved_at = datetime.now(UTC)
    user.failed_logins = 0
    user.locked_until = None
    await db.commit()
    await db.refresh(user)
    return (await _user_summaries(db, [user]))[0]


@router.post("/users/{user_id}/verify-email", response_model=AdminUserOut)
async def verify_user_email(
    user_id: int,
    db: DB,
    body: AdminVerifyEmailIn | None = None,
) -> AdminUserOut:
    """Allow admins to manually verify a user's email (and optionally approve pending users)."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    now = datetime.now(UTC)
    if user.email_verified_at is None:
        user.email_verified_at = now
    if body is None or body.approve:
        if user.approved_at is None:
            user.approved_at = now
        user.is_active = True
        user.failed_logins = 0
        user.locked_until = None
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == "verify_email",
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    await db.commit()
    await db.refresh(user)
    return (await _user_summaries(db, [user]))[0]


@router.post("/users/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_user(user_id: int, admin: AdminUser, db: DB) -> None:
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot reject your own account.")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if user.approved_at is not None or user.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only pending registration requests can be rejected.",
        )
    await db.delete(user)
    await db.commit()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, admin: AdminUser, db: DB) -> None:
    if user_id == admin.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "You cannot delete your own account."
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if user.role == "admin" and user.is_active and await _active_admin_count(db) <= 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "At least one active administrator is required."
        )
    await db.delete(user)
    await db.commit()


# ---- dashboard metrics ---------------------------------------------------------------------


@router.get("/metrics")
async def get_metrics(db: DB) -> dict:
    now = datetime.now(UTC)
    day_ago = now - timedelta(hours=24)
    week_ago = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)

    total_users, active_users, pending_users, admin_users, logins_24h = (
        await db.execute(
            select(
                func.count(),
                func.sum(case((User.is_active.is_(True), 1), else_=0)),
                func.sum(
                    case(
                        ((User.is_active.is_(False)) & (User.approved_at.is_(None)), 1),
                        else_=0,
                    )
                ),
                func.sum(case((User.role == "admin", 1), else_=0)),
                func.sum(case((User.last_login_at >= day_ago, 1), else_=0)),
            ).select_from(User)
        )
    ).one()
    active_sessions = (
        await db.scalar(
            select(func.count())
            .select_from(UserSession)
            .where(UserSession.revoked_at.is_(None), UserSession.expires_at > now)
        )
    ) or 0

    total_convs = (await db.scalar(select(func.count()).select_from(Conversation))) or 0
    total_msgs, user_msgs, assistant_msgs, msgs_24h, avg_lat, avg_ft, not_found_cnt = (
        await db.execute(
            select(
                func.count(),
                func.sum(case((Message.role == "user", 1), else_=0)),
                func.sum(case((Message.role == "assistant", 1), else_=0)),
                func.sum(case((Message.created_at >= day_ago, 1), else_=0)),
                func.avg(Message.latency_ms),
                func.avg(Message.first_token_ms),
                func.sum(case((Message.not_found.is_(True), 1), else_=0)),
            ).select_from(Message)
        )
    ).one()

    intent_rows = (
        await db.execute(
            select(Message.intent, func.count())
            .where(Message.role == "assistant", Message.intent.is_not(None))
            .group_by(Message.intent)
        )
    ).all()
    by_intent = {k: int(v) for k, v in intent_rows if k}

    total_sites, enabled_sites = (
        await db.execute(
            select(
                func.count(),
                func.sum(case((Site.enabled.is_(True), 1), else_=0)),
            ).select_from(Site)
        )
    ).one()
    pages_active, pages_gone = (
        await db.execute(
            select(
                func.sum(case((Page.status == "active", 1), else_=0)),
                func.sum(case((Page.status == "gone", 1), else_=0)),
            ).select_from(Page)
        )
    ).one()
    chunks_total, chunks_embedded = (
        await db.execute(
            select(
                func.count(Chunk.id),
                func.sum(case((Chunk.embedding.is_not(None), 1), else_=0)),
            )
            .join(Page, Page.id == Chunk.page_id)
            .where(Page.status == "active", Chunk.page_version == Page.version)
        )
    ).one()
    jobs_running, jobs_queued, jobs_failed = (
        await db.execute(
            select(
                func.sum(case((CrawlJob.status == "running", 1), else_=0)),
                func.sum(case((CrawlJob.status == "queued", 1), else_=0)),
                func.sum(case((CrawlJob.status == "failed", 1), else_=0)),
            ).select_from(CrawlJob)
        )
    ).one()

    total_dbs, enabled_dbs, pg_dbs, my_dbs = (
        await db.execute(
            select(
                func.count(),
                func.sum(case((DataSource.enabled.is_(True), 1), else_=0)),
                func.sum(case((DataSource.kind == "postgres", 1), else_=0)),
                func.sum(case((DataSource.kind == "mysql", 1), else_=0)),
            ).select_from(DataSource)
        )
    ).one()
    policy_rows = (
        await db.execute(
            select(DataSource.edit_policy, func.count()).group_by(DataSource.edit_policy)
        )
    ).all()
    by_policy = {k: int(v) for k, v in policy_rows}

    q_total, q_ok, q_refused, q_error = (
        await db.execute(
            select(
                func.count(),
                func.sum(case((DsQueryLog.status.in_(("ok", "applied")), 1), else_=0)),
                func.sum(case((DsQueryLog.status == "refused", 1), else_=0)),
                func.sum(case((DsQueryLog.status == "error", 1), else_=0)),
            ).select_from(DsQueryLog)
        )
    ).one()
    pending_approvals = (
        await db.scalar(
            select(func.count())
            .select_from(DsPendingChange)
            .where(DsPendingChange.status == "pending", DsPendingChange.expires_at > now)
        )
    ) or 0

    msg_daily = dict(
        (
            await db.execute(
                select(func.date(Message.created_at), func.count())
                .where(Message.created_at >= week_ago, Message.role == "user")
                .group_by(func.date(Message.created_at))
            )
        ).all()
    )
    query_daily = dict(
        (
            await db.execute(
                select(func.date(DsQueryLog.created_at), func.count())
                .where(DsQueryLog.created_at >= week_ago)
                .group_by(func.date(DsQueryLog.created_at))
            )
        ).all()
    )
    daily_7d = []
    for offset in range(7):
        d = (week_ago + timedelta(days=offset)).date()
        daily_7d.append(
            {
                "date": d.isoformat(),
                "messages": int(msg_daily.get(d, 0) or 0),
                "queries": int(query_daily.get(d, 0) or 0),
            }
        )

    return {
        "users": {
            "total": int(total_users or 0),
            "active": int(active_users or 0),
            "pending_approval": int(pending_users or 0),
            "admins": int(admin_users or 0),
            "members": int((total_users or 0) - (admin_users or 0)),
            "active_sessions": int(active_sessions),
            "logins_24h": int(logins_24h or 0),
        },
        "chats": {
            "conversations": int(total_convs),
            "messages": int(total_msgs or 0),
            "user_messages": int(user_msgs or 0),
            "assistant_messages": int(assistant_msgs or 0),
            "messages_24h": int(msgs_24h or 0),
            "avg_latency_ms": round(float(avg_lat)) if avg_lat is not None else None,
            "avg_first_token_ms": round(float(avg_ft)) if avg_ft is not None else None,
            "not_found_count": int(not_found_cnt or 0),
            "by_intent": by_intent,
            "daily_7d": daily_7d,
        },
        "sites": {
            "total": int(total_sites or 0),
            "enabled": int(enabled_sites or 0),
            "pages_active": int(pages_active or 0),
            "pages_gone": int(pages_gone or 0),
            "chunks": int(chunks_total or 0),
            "chunks_embedded": int(chunks_embedded or 0),
            "jobs_running": int(jobs_running or 0),
            "jobs_queued": int(jobs_queued or 0),
            "jobs_failed": int(jobs_failed or 0),
        },
        "databases": {
            "total": int(total_dbs or 0),
            "enabled": int(enabled_dbs or 0),
            "postgres": int(pg_dbs or 0),
            "mysql": int(my_dbs or 0),
            "by_policy": by_policy,
            "queries_total": int(q_total or 0),
            "queries_ok": int(q_ok or 0),
            "queries_refused": int(q_refused or 0),
            "queries_error": int(q_error or 0),
            "pending_approvals": int(pending_approvals),
        },
    }


# ---- system settings -----------------------------------------------------------------------


@router.get("/settings", response_model=SystemSettingsData)
async def read_system_settings(db: DB) -> SystemSettingsData:
    return await get_system_settings(db, get_settings())


@router.put("/settings", response_model=SystemSettingsData)
async def write_system_settings(body: SystemSettingsData, db: DB) -> SystemSettingsData:
    return await save_system_settings(db, body)


# ---- legal documents & operator metadata ---------------------------------------------------


@router.get("/legal", response_model=LegalSettingsData)
async def read_admin_legal(db: DB) -> LegalSettingsData:
    return await get_legal_settings(db)


@router.put("/legal", response_model=LegalSettingsData)
async def write_admin_legal(body: LegalSettingsData, db: DB) -> LegalSettingsData:
    return await save_legal_settings(db, body)


@router.post("/legal/reset", response_model=LegalSettingsData)
async def reset_admin_legal(db: DB) -> LegalSettingsData:
    return await reset_legal_settings(db)

