"""Grounded Q&A over the configured sites and databases (per user).

Streamed as SSE events: status, sources, token, then done (or error).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.chat import ChatMessage, ChatRequest
from app.api.conversations import persist_sse_stream, prepare_conversation_turn
from app.auth.deps import current_user
from app.config import get_settings
from app.datasources.local import LocalSourceIn, to_ephemeral_source
from app.db.models import User
from app.db.session import SessionLocal, get_session
from app.llm.chat import ChatTarget, get_chat
from app.llm.embed import get_embedder
from app.llm.rerank import get_reranker
from app.orchestrator.answer import answer_stream
from app.orchestrator.memory import (
    ASK_HISTORY_CHARS,
    ASK_RECENT_MESSAGES,
    append_memory_sse,
    compact_prepared_conversation,
    compact_stateless_messages,
)
from app.settings_store import apply_runtime_settings, get_system_settings

router = APIRouter(tags=["ask"])


class AskRequest(ChatRequest):
    """Inherits ChatRequest's validation (the last message must be the user's question)."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    # None = everything enabled. A list (even an empty one) = only those, so a user who picked
    # only a database chip doesn't get websites mixed in (and the other way round).
    site_ids: list[int] | None = Field(None, max_length=200)
    source_ids: list[int] | None = Field(None, max_length=200)
    local_sources: list[LocalSourceIn] = Field(default_factory=list, max_length=20)
    max_tokens: int | None = Field(None, ge=1, le=4096)  # None = no limit (null to vLLM)


@router.post("/ask")
async def ask(
    req: AskRequest,
    request: Request,
    chat: Annotated[ChatTarget, Depends(get_chat)],
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    site_ids, source_ids = req.site_ids, req.source_ids
    if site_ids is not None and source_ids is None:
        source_ids = []  # an older client that only knows about sites: keep its scope

    sys_cfg = await get_system_settings(db, get_settings())
    runtime_settings = apply_runtime_settings(get_settings(), sys_cfg)

    ask_budget = min(sys_cfg.max_history_chars, ASK_HISTORY_CHARS)
    persist = bool(req.conversation_id) and user.storage_mode != "local"
    if persist and req.conversation_id:
        selected = req.selected_sources
        if selected is None and (site_ids is not None or source_ids is not None):
            selected = [*(site_ids or []), *(-s for s in (source_ids or []))]
        prepared = await prepare_conversation_turn(
            db,
            user_id=user.id,
            conversation_id=req.conversation_id,
            user_text=req.messages[-1].content,
            mode="sites",
            selected_sources=selected,
        )
        compacted = await compact_prepared_conversation(
            prepared,
            target=chat,
            db=db,
            char_budget=ask_budget,
            max_recent_messages=ASK_RECENT_MESSAGES,
        )
    else:
        compacted = await compact_stateless_messages(
            [m.model_dump() for m in req.messages],
            target=chat,
            char_budget=ask_budget,
            max_recent_messages=ASK_RECENT_MESSAGES,
            existing_summary=req.conversation_summary,
        )

    local_dbs = [
        to_ephemeral_source(ls, user.id)
        for ls in req.local_sources
        if ls.enabled and (source_ids is None or ls.id in source_ids)
    ]

    stream = answer_stream(
        compacted.turns,
        site_ids,
        settings=runtime_settings,
        sessions=SessionLocal,
        chat=chat,
        embedder=get_embedder(),
        reranker=get_reranker(),
        live=getattr(request.app.state, "live", None),  # always live in app lifespan
        max_tokens=req.max_tokens,
        source_ids=source_ids,
        user_id=user.id,
        owner_id=user.id,
        system_prompt=sys_cfg.answer_system_prompt,
        conversation_summary=compacted.summary,
        local_dbs=local_dbs,
    )
    if persist and req.conversation_id:
        stream = persist_sse_stream(
            stream,
            sessions=SessionLocal,
            conversation_id=req.conversation_id,
            mode="sites",
        )
    else:
        stream = append_memory_sse(
            stream,
            compacted.summary,
            emit=compacted.compacted_count > 0,
        )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
