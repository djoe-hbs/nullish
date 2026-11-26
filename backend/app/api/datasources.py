"""Database connections: add / test / edit / delete, schema (notes, hidden), query log, and the
approval of pending changes. Passwords are write-only: stored encrypted, never returned."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.config import get_settings
from app.datasources.connectors import DEFAULT_PORTS, DbError, connect
from app.datasources.hosts import HostNotAllowed
from app.datasources.schema import hidden_of, load_schema, refresh_schema
from app.datasources.sqlgate import check, decide, sql_hash
from app.db.models import DataSource, DsPendingChange, DsQueryLog, DsSchemaItem, User
from app.db.session import get_session
from app.llm.embed import get_embedder
from app.security.crypto import SecretUnreadable, encrypt

router = APIRouter(tags=["databases"])

DB = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[User, Depends(current_user)]


# ---- schemas -------------------------------------------------------------------------------


class SourceSettings(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    host: str | None = Field(None, min_length=1, max_length=255)
    port: int | None = Field(None, ge=1, le=65535)
    database: str | None = Field(None, min_length=1, max_length=255)
    username: str | None = Field(None, min_length=1, max_length=255)
    password: str | None = Field(None, max_length=1000)  # write-only; None = keep the old one
    ssl_mode: Literal["disable", "prefer", "require", "verify"] | None = None
    edit_policy: Literal["read_only", "ask", "auto"] | None = None
    row_limit: int | None = Field(None, ge=1, le=5000)
    timeout_sec: int | None = Field(None, ge=1, le=300)
    enabled: bool | None = None

    @field_validator("host")
    @classmethod
    def clean_host(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class SourceCreate(SourceSettings):
    kind: Literal["postgres", "mysql"]
    host: str = Field(min_length=1, max_length=255)
    database: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)


class SourceOut(BaseModel):
    id: int
    name: str
    kind: str
    host: str
    port: int
    database: str
    username: str
    has_password: bool
    ssl_mode: str
    edit_policy: str
    row_limit: int
    timeout_sec: int
    description: str | None
    enabled: bool
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_error: str | None
    server_version: str | None
    schema_refreshed_at: datetime | None
    schema_error: str | None
    tables: int = 0
    created_at: datetime


class TestResult(BaseModel):
    ok: bool
    version: str | None = None
    error: str | None = None


class SchemaItemOut(BaseModel):
    id: int
    schema_name: str
    table_name: str
    column_name: str | None
    data_type: str | None
    nullable: bool | None
    is_pk: bool
    fk_ref: str | None
    row_estimate: int | None
    sample_values: list
    comment: str | None
    note: str | None
    hidden: bool


class SchemaPatch(BaseModel):
    id: int
    note: str | None = Field(None, max_length=2000)
    hidden: bool | None = None


class LogOut(BaseModel):
    id: int
    sql: str
    kind: str
    status: str
    rows: int | None
    ms: int | None
    error: str | None
    pending_id: int | None
    created_at: datetime


class ChangeOut(BaseModel):
    id: int
    source_id: int
    source: str
    sql: str
    kind: str
    reason: str | None
    preview: dict
    status: str
    result: dict | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


def out(src: DataSource, tables: int = 0) -> SourceOut:
    return SourceOut(
        id=src.id, name=src.name, kind=src.kind, host=src.host, port=src.port,
        database=src.database, username=src.username,
        has_password=bool(src.password_encrypted), ssl_mode=src.ssl_mode,
        edit_policy=src.edit_policy, row_limit=src.row_limit, timeout_sec=src.timeout_sec,
        description=src.description, enabled=src.enabled, last_test_at=src.last_test_at,
        last_test_ok=src.last_test_ok, last_error=src.last_error,
        server_version=src.server_version, schema_refreshed_at=src.schema_refreshed_at,
        schema_error=src.schema_error, tables=tables, created_at=src.created_at)


async def get_source(db: AsyncSession, source_id: int) -> DataSource:
    src = await db.get(DataSource, source_id)
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "database not found")
    return src


async def table_counts(db: AsyncSession) -> dict[int, int]:
    rows = await db.execute(
        select(DsSchemaItem.source_id, func.count())
        .where(DsSchemaItem.column_name.is_(None)).group_by(DsSchemaItem.source_id))
    return dict(rows.all())


async def run_test(src: DataSource, password: str | None = None) -> TestResult:
    try:
        async with connect(src, get_settings(), password=password) as conn:
            return TestResult(ok=True, version=await conn.version())
    except (DbError, HostNotAllowed, SecretUnreadable) as exc:
        return TestResult(ok=False, error=str(exc))


async def record_test(db: AsyncSession, src: DataSource, res: TestResult) -> None:
    src.last_test_at = datetime.now(UTC)
    src.last_test_ok = res.ok
    src.last_error = res.error
    if res.version:
        src.server_version = res.version
    await db.commit()


# ---- connections -----------------------------------------------------------------------------


@router.get("/data-sources", response_model=list[SourceOut])
async def list_sources(db: DB) -> list[SourceOut]:
    counts = await table_counts(db)
    srcs = (await db.scalars(select(DataSource).order_by(DataSource.id))).all()
    return [out(s, counts.get(s.id, 0)) for s in srcs]


@router.post("/data-sources/test", response_model=TestResult)
async def test_unsaved(body: SourceCreate) -> TestResult:
    """Try a connection before saving it."""
    src = DataSource(kind=body.kind, host=body.host,
                     port=body.port or DEFAULT_PORTS[body.kind], database=body.database,
                     username=body.username, ssl_mode=body.ssl_mode or "prefer",
                     timeout_sec=body.timeout_sec or 15)
    return await run_test(src, password=body.password or "")


@router.post("/data-sources", response_model=SourceOut, status_code=201)
async def create_source(body: SourceCreate, db: DB) -> SourceOut:
    src = DataSource(
        name=(body.name or f"{body.database} on {body.host}").strip()[:200], kind=body.kind,
        host=body.host, port=body.port or DEFAULT_PORTS[body.kind], database=body.database,
        username=body.username,
        password_encrypted=encrypt(body.password) if body.password else None,
        ssl_mode=body.ssl_mode or "prefer", edit_policy=body.edit_policy or "read_only",
        row_limit=body.row_limit or 200, timeout_sec=body.timeout_sec or 15,
        description=body.description, enabled=True if body.enabled is None else body.enabled)
    db.add(src)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a database with that name exists") from exc
    res = await run_test(src)
    await record_test(db, src, res)
    tables = 0
    if res.ok:
        try:
            tables = await refresh_schema(db, src, get_settings(), get_embedder())
        except DbError:
            pass  # shown as schema_error on the card
    return out(src, tables)


@router.patch("/data-sources/{source_id}", response_model=SourceOut)
async def update_source(source_id: int, body: SourceSettings, db: DB) -> SourceOut:
    src = await get_source(db, source_id)
    data = body.model_dump(exclude_unset=True)
    reconnect = any(k in data for k in ("host", "port", "database", "username", "password",
                                        "ssl_mode"))
    if "password" in data:
        pw = data.pop("password")
        src.password_encrypted = encrypt(pw) if pw else None
    for k, v in data.items():
        if v is not None or k == "description":
            setattr(src, k, v)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a database with that name exists") from exc
    if reconnect:
        await record_test(db, src, await run_test(src))
    return out(src, (await table_counts(db)).get(src.id, 0))


@router.delete("/data-sources/{source_id}", status_code=204)
async def delete_source(source_id: int, db: DB) -> None:
    src = await get_source(db, source_id)
    await db.delete(src)
    await db.commit()


@router.post("/data-sources/{source_id}/test", response_model=TestResult)
async def test_saved(source_id: int, db: DB) -> TestResult:
    src = await get_source(db, source_id)
    res = await run_test(src)
    await record_test(db, src, res)
    return res


@router.post("/data-sources/{source_id}/refresh-schema", response_model=SourceOut)
async def refresh(source_id: int, db: DB) -> SourceOut:
    src = await get_source(db, source_id)
    try:
        tables = await refresh_schema(db, src, get_settings(), get_embedder())
    except (DbError, HostNotAllowed, SecretUnreadable) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return out(src, tables)


# ---- schema ----------------------------------------------------------------------------------


@router.get("/data-sources/{source_id}/schema", response_model=list[SchemaItemOut])
async def get_schema(source_id: int, db: DB) -> list[SchemaItemOut]:
    await get_source(db, source_id)
    items = (await db.scalars(
        select(DsSchemaItem).where(DsSchemaItem.source_id == source_id)
        .order_by(DsSchemaItem.schema_name, DsSchemaItem.table_name,
                  DsSchemaItem.column_name.isnot(None), DsSchemaItem.ordinal))).all()
    return [SchemaItemOut.model_validate(i, from_attributes=True) for i in items]


@router.patch("/data-sources/{source_id}/schema", response_model=list[SchemaItemOut])
async def patch_schema(source_id: int, body: list[SchemaPatch], db: DB) -> list[SchemaItemOut]:
    await get_source(db, source_id)
    ids = [p.id for p in body]
    items = {i.id: i for i in (await db.scalars(
        select(DsSchemaItem).where(DsSchemaItem.source_id == source_id,
                                   DsSchemaItem.id.in_(ids or [-1])))).all()}
    for p in body:
        item = items.get(p.id)
        if item is None:
            continue
        fields = p.model_dump(exclude_unset=True)
        if "note" in fields:
            item.note = (p.note or "").strip() or None
        if p.hidden is not None:
            item.hidden = p.hidden
    await db.commit()
    return await get_schema(source_id, db)


# ---- audit log -------------------------------------------------------------------------------


@router.get("/data-sources/{source_id}/log", response_model=list[LogOut])
async def query_log(source_id: int, db: DB,
                    limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[LogOut]:
    await get_source(db, source_id)
    rows = (await db.scalars(
        select(DsQueryLog).where(DsQueryLog.source_id == source_id)
        .order_by(DsQueryLog.id.desc()).limit(limit))).all()
    return [LogOut.model_validate(r, from_attributes=True) for r in rows]


# ---- pending changes (approval) --------------------------------------------------------------


def change_out(c: DsPendingChange, src_name: str) -> ChangeOut:
    status_ = c.status
    if status_ == "pending" and c.expires_at <= datetime.now(UTC):
        status_ = "expired"
    return ChangeOut(id=c.id, source_id=c.source_id, source=src_name, sql=c.sql, kind=c.kind,
                     reason=c.reason, preview=c.preview, status=status_, result=c.result,
                     created_at=c.created_at, expires_at=c.expires_at, decided_at=c.decided_at)


async def get_change(db: AsyncSession, change_id: int) -> tuple[DsPendingChange, DataSource]:
    c = await db.get(DsPendingChange, change_id, with_for_update=True)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change not found")
    return c, await get_source(db, c.source_id)


@router.get("/changes/{change_id}", response_model=ChangeOut)
async def get_pending(change_id: int, db: DB) -> ChangeOut:
    c, src = await get_change(db, change_id)
    return change_out(c, src.name)


@router.post("/changes/{change_id}/approve", response_model=ChangeOut)
async def approve(change_id: int, db: DB, me: Me) -> ChangeOut:
    c, src = await get_change(db, change_id)
    if c.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"this change is already {c.status}")
    now = datetime.now(UTC)
    if c.expires_at <= now:
        c.status = "expired"
        await db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this change expired; ask again to get a fresh preview")
    if sql_hash(c.sql) != c.sql_hash:
        raise HTTPException(status.HTTP_409_CONFLICT, "the stored change was altered; refused")
    # Check again with today's rules: the permission or hidden tables may have changed since.
    gate = check(c.sql, src.kind, row_limit=src.row_limit, hidden=hidden_of(
        await load_schema(db, src)))
    if gate.kind not in ("write", "ddl") or decide(gate.kind, src.edit_policy) == "refuse":
        reason = gate.reason or ("this database is now set to Never edit"
                                 if src.edit_policy == "read_only" else "not allowed")
        raise HTTPException(status.HTTP_409_CONFLICT, f"refused: {reason}")

    c.decided_at, c.decided_by = now, me.id
    log = DsQueryLog(source_id=src.id, user_id=me.id, sql=c.sql, kind=gate.kind,
                     pending_id=c.id, status="applied")
    try:
        async with connect(src, get_settings()) as conn:
            res = await conn.execute(c.sql)
        c.status = "applied"
        c.result = {"rowcount": res.rowcount, "ms": res.ms}
        log.rows, log.ms = res.rowcount, res.ms
    except (DbError, HostNotAllowed, SecretUnreadable) as exc:
        c.status = "failed"
        c.result = {"error": str(exc)}
        log.status, log.error = "error", str(exc)
    db.add(log)
    await db.commit()
    if gate.kind == "ddl" and c.status == "applied":
        try:
            await refresh_schema(db, src, get_settings(), get_embedder())
        except Exception:  # noqa: BLE001, S110 - the change itself succeeded
            pass
    return change_out(c, src.name)


@router.post("/changes/{change_id}/reject", response_model=ChangeOut)
async def reject(change_id: int, db: DB, me: Me) -> ChangeOut:
    c, src = await get_change(db, change_id)
    if c.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"this change is already {c.status}")
    c.status, c.decided_at, c.decided_by = "rejected", datetime.now(UTC), me.id
    db.add(DsQueryLog(source_id=src.id, user_id=me.id, sql=c.sql, kind=c.kind,
                      status="rejected", pending_id=c.id))
    await db.commit()
    return change_out(c, src.name)
