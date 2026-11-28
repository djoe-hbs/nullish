"""PostgreSQL-backed conversations and messages (strictly private to the owning user)."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import current_user
from app.db.models import Conversation, Message, User
from app.db.session import get_session
from app.orchestrator.memory import PreparedConversation, TurnRecord

router = APIRouter(prefix="/conversations", tags=["conversations"])

DB = Annotated[AsyncSession, Depends(get_session)]
Me = Annotated[User, Depends(current_user)]


class ConversationOut(BaseModel):
    id: str
    name: str
    title_source: str
    mode: str
    selected_sources: list[int]
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    mode: str | None = None
    error: str | None = None
    sources: list[Any] | None = None
    queries: list[Any] | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = []


class ConversationCreate(BaseModel):
    id: str | None = Field(None, min_length=8, max_length=36)
    name: str | None = Field(None, max_length=120)
    title_source: Literal["auto", "llm", "user"] = "auto"
    mode: Literal["sites", "model"] = "sites"
    selected_sources: list[int] = Field(default_factory=list, max_length=200)


class ConversationPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    title_source: Literal["auto", "llm", "user"] | None = None
    mode: Literal["sites", "model"] | None = None
    selected_sources: list[int] | None = Field(None, max_length=200)


class ImportMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=100_000)
    mode: Literal["sites", "model"] | None = None
    error: str | None = None
    sources: list[Any] | None = None
    queries: list[Any] | None = None
    meta: dict[str, Any] | None = None


class ImportConversation(BaseModel):
    id: str = Field(min_length=8, max_length=36)
    name: str = Field(min_length=1, max_length=120)
    title_source: Literal["auto", "llm", "user"] = "auto"
    mode: Literal["sites", "model"] = "sites"
    selected_sources: list[int] = Field(default_factory=list, max_length=200)
    updated_ms: int | None = None
    messages: list[ImportMessage] = Field(default_factory=list, max_length=400)


def _clean_name(raw: str | None, fallback: str = "New chat") -> str:
    cleaned = (raw or "").strip()[:120]
    return cleaned or fallback


async def get_owned_conversation(
    db: AsyncSession, conv_id: str, user_id: int
) -> Conversation:
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return conv


LOCAL_STORAGE_MSG = (
    "Your account uses Browser Local Storage. Conversations are kept in your browser and not "
    "saved to the server database."
)


def _require_db_storage(user: User) -> None:
    if user.storage_mode == "local":
        raise HTTPException(status.HTTP_409_CONFLICT, LOCAL_STORAGE_MSG)


@router.get("", response_model=list[ConversationOut])
async def list_conversations(db: DB, user: Me) -> list[ConversationOut]:
    if user.storage_mode == "local":
        return []
    convs = (
        await db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc())
        )
    ).all()
    if not convs:
        return []
    ids = [c.id for c in convs]
    counts = dict(
        (
            await db.execute(
                select(Message.conversation_id, func.count())
                .where(Message.conversation_id.in_(ids))
                .group_by(Message.conversation_id)
            )
        ).all()
    )
    return [
        ConversationOut(
            id=c.id,
            name=c.name,
            title_source=c.title_source,
            mode=c.mode,
            selected_sources=list(c.selected_sources or []),
            created_at=c.created_at,
            updated_at=c.updated_at,
            message_count=int(counts.get(c.id, 0)),
        )
        for c in convs
    ]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(body: ConversationCreate, db: DB, user: Me) -> ConversationOut:
    _require_db_storage(user)
    cid = body.id or uuid4().hex
    existing = await db.get(Conversation, cid)
    if existing is not None:
        if existing.user_id == user.id:
            count = (
                await db.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == existing.id)
                )
            ) or 0
            return ConversationOut(
                id=existing.id,
                name=existing.name,
                title_source=existing.title_source,
                mode=existing.mode,
                selected_sources=list(existing.selected_sources or []),
                created_at=existing.created_at,
                updated_at=existing.updated_at,
                message_count=int(count),
            )
        cid = uuid4().hex
    now = datetime.now(UTC)
    conv = Conversation(
        id=cid,
        user_id=user.id,
        name=_clean_name(body.name),
        title_source=body.title_source,
        mode=body.mode,
        selected_sources=body.selected_sources,
        updated_at=now,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ConversationOut(
        id=conv.id,
        name=conv.name,
        title_source=conv.title_source,
        mode=conv.mode,
        selected_sources=list(conv.selected_sources or []),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=0,
    )


@router.get("/{conv_id}", response_model=ConversationDetail)
async def get_conversation(conv_id: str, db: DB, user: Me) -> ConversationDetail:
    conv = await get_owned_conversation(db, conv_id, user.id)
    msgs = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.id)
        )
    ).all()
    return ConversationDetail(
        id=conv.id,
        name=conv.name,
        title_source=conv.title_source,
        mode=conv.mode,
        selected_sources=list(conv.selected_sources or []),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=len(msgs),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


@router.patch("/{conv_id}", response_model=ConversationOut)
async def update_conversation(
    conv_id: str, body: ConversationPatch, db: DB, user: Me
) -> ConversationOut:
    _require_db_storage(user)
    conv = await get_owned_conversation(db, conv_id, user.id)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        conv.name = _clean_name(fields["name"], conv.name)
        if "title_source" not in fields:
            conv.title_source = "user"
    if "title_source" in fields and fields["title_source"] is not None:
        conv.title_source = fields["title_source"]
    if "mode" in fields and fields["mode"] is not None:
        conv.mode = fields["mode"]
    if "selected_sources" in fields and fields["selected_sources"] is not None:
        conv.selected_sources = fields["selected_sources"]
    await db.commit()
    await db.refresh(conv)
    count = (
        await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conv.id)
        )
    ) or 0
    return ConversationOut(
        id=conv.id,
        name=conv.name,
        title_source=conv.title_source,
        mode=conv.mode,
        selected_sources=list(conv.selected_sources or []),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=int(count),
    )


@router.delete("/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conv_id: str, db: DB, user: Me) -> None:
    conv = await get_owned_conversation(db, conv_id, user.id)
    await db.delete(conv)
    await db.commit()


@router.post("/import", response_model=list[ConversationOut])
async def import_conversations(
    items: list[ImportConversation], db: DB, user: Me
) -> list[ConversationOut]:
    """One-time migration helper for conversations previously saved in browser localStorage."""
    _require_db_storage(user)
    created: list[ConversationOut] = []
    for item in items[:100]:
        if not item.messages:
            continue
        cid = item.id
        existing = await db.get(Conversation, cid)
        if existing is not None:
            if existing.user_id == user.id:
                continue  # already imported
            cid = uuid4().hex
        ts = (
            datetime.fromtimestamp(item.updated_ms / 1000, tz=UTC)
            if item.updated_ms
            else datetime.now(UTC)
        )
        conv = Conversation(
            id=cid,
            user_id=user.id,
            name=_clean_name(item.name),
            title_source=item.title_source,
            mode=item.mode,
            selected_sources=item.selected_sources,
            created_at=ts,
            updated_at=ts,
        )
        db.add(conv)
        await db.flush()
        for m in item.messages:
            meta = m.meta if isinstance(m.meta, dict) else None
            db.add(
                Message(
                    conversation_id=conv.id,
                    role=m.role,
                    content=m.content,
                    mode=m.mode or item.mode,
                    error=m.error,
                    sources=m.sources,
                    queries=m.queries,
                    meta=meta,
                    intent=meta.get("intent") if meta else ("model" if m.mode == "model" else None),
                    latency_ms=meta.get("latency_ms") if meta else None,
                    first_token_ms=meta.get("first_token_ms") if meta else None,
                    not_found=meta.get("not_found") if meta else None,
                )
            )
        created.append(
            ConversationOut(
                id=conv.id,
                name=conv.name,
                title_source=conv.title_source,
                mode=conv.mode,
                selected_sources=list(conv.selected_sources or []),
                created_at=ts,
                updated_at=ts,
                message_count=len(item.messages),
            )
        )
    await db.commit()
    return created


async def prepare_conversation_turn(
    db: AsyncSession,
    *,
    user_id: int,
    conversation_id: str,
    user_text: str,
    mode: Literal["sites", "model"],
    selected_sources: list[int] | None = None,
) -> PreparedConversation:
    """Verify/create conversation owned by user, append the user's message, and return all
    unsummarized turns plus any existing rolling summary."""
    now = datetime.now(UTC)
    conv = await db.get(Conversation, conversation_id)
    if conv is not None and conv.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    if conv is None:
        conv = Conversation(
            id=conversation_id,
            user_id=user_id,
            name=_clean_name(user_text[:70], "New chat"),
            title_source="auto",
            mode=mode,
            selected_sources=selected_sources or [],
            updated_at=now,
        )
        db.add(conv)
        await db.flush()
    else:
        conv.mode = mode
        if selected_sources is not None:
            conv.selected_sources = selected_sources
        conv.updated_at = now

    stmt = select(Message.id, Message.role, Message.content).where(
        Message.conversation_id == conv.id,
        Message.error.is_(None),
        Message.content != "",
    )
    if conv.summary_through_message_id is not None:
        stmt = stmt.where(Message.id > conv.summary_through_message_id)
    prior_rows = (await db.execute(stmt.order_by(Message.id))).all()
    prior_turns = [
        TurnRecord(id=mid, role=role, content=content) for mid, role, content in prior_rows
    ]
    existing_summary = conv.summary
    through_id = conv.summary_through_message_id

    db.add(Message(conversation_id=conv.id, role="user", content=user_text, mode=mode))
    await db.commit()
    return PreparedConversation(
        conversation_id=conv.id,
        summary=existing_summary,
        summary_through_message_id=through_id,
        prior_turns=prior_turns,
        user_text=user_text,
    )


async def persist_sse_stream(
    stream: AsyncIterator[str],
    *,
    sessions: async_sessionmaker[AsyncSession],
    conversation_id: str,
    mode: Literal["sites", "model"],
) -> AsyncIterator[str]:
    """Forward SSE events while capturing assistant output and saving it to Postgres."""
    content = ""
    sources: list[Any] | None = None
    queries: list[Any] = []
    meta: dict[str, Any] | None = None
    error: str | None = None
    saved = False

    async def save() -> int | None:
        nonlocal saved
        if saved:
            return None
        saved = True
        now = datetime.now(UTC)
        intent = (
            meta.get("intent")
            if isinstance(meta, dict) and meta.get("intent")
            else ("model" if mode == "model" else None)
        )
        async with sessions() as db:
            msg = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                mode=mode,
                error=error,
                sources=sources,
                queries=queries or None,
                meta=meta,
                intent=intent,
                latency_ms=meta.get("latency_ms") if isinstance(meta, dict) else None,
                first_token_ms=meta.get("first_token_ms") if isinstance(meta, dict) else None,
                not_found=meta.get("not_found") if isinstance(meta, dict) else None,
            )
            db.add(msg)
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=now)
            )
            await db.commit()
            await db.refresh(msg)
            return msg.id

    try:
        async for raw in stream:
            lines = raw.strip().split("\n")
            ev = ""
            data_str = ""
            for line in lines:
                if line.startswith("event: "):
                    ev = line[7:]
                elif line.startswith("data: "):
                    data_str += line[6:]
            if data_str:
                payload = json.loads(data_str)
                if ev == "token":
                    content += payload.get("t", "")
                elif ev == "replace":
                    content = payload.get("t", "")
                elif ev == "sources":
                    sources = payload
                elif ev == "query":
                    queries.append(payload)
                elif ev == "error":
                    error = payload.get("message")
                elif ev == "done":
                    meta = payload
                    msg_id = await save()
                    if msg_id is not None and isinstance(payload, dict):
                        payload["message_id"] = msg_id
                        raw = (
                            f"event: done\ndata: "
                            f"{json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                        )
            yield raw
    except asyncio.CancelledError:
        error = error or ("Stopped." if content else "Stopped before the first token.")
        try:
            await asyncio.shield(save())
        except asyncio.CancelledError:
            pass
        raise
    except Exception as exc:
        error = error or f"{type(exc).__name__}: {exc}"
        await save()
        raise
    finally:
        if not saved and (content or error or sources or queries or meta):
            try:
                await asyncio.shield(save())
            except asyncio.CancelledError:
                pass

