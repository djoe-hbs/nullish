"""Grounded Q&A over the configured sites.

Streamed as SSE events: status, sources, token, then done (or error).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.api.chat import ChatMessage, ChatRequest
from app.auth.deps import current_user
from app.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal
from app.llm.chat import ChatTarget, get_chat
from app.llm.embed import get_embedder
from app.llm.rerank import get_reranker
from app.orchestrator.answer import answer_stream

router = APIRouter(tags=["ask"])


class AskRequest(ChatRequest):
    """Inherits ChatRequest's validation (the last message must be the user's question)."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    # None = everything enabled. A list (even an empty one) = only those, so a user who picked
    # only a database chip doesn't get websites mixed in (and the other way round).
    site_ids: list[int] | None = Field(None, max_length=200)
    source_ids: list[int] | None = Field(None, max_length=200)
    max_tokens: int | None = Field(None, ge=1, le=4096)  # None = no limit (null to vLLM)


@router.post("/ask")
async def ask(
    req: AskRequest, request: Request, chat: Annotated[ChatTarget, Depends(get_chat)],
    user: Annotated[User, Depends(current_user)],
) -> StreamingResponse:
    site_ids, source_ids = req.site_ids, req.source_ids
    if site_ids is not None and source_ids is None:
        source_ids = []  # an older client that only knows about sites: keep its scope
    return StreamingResponse(
        answer_stream(
            [m.model_dump() for m in req.messages],
            site_ids,
            settings=get_settings(),
            sessions=SessionLocal,
            chat=chat,
            embedder=get_embedder(),
            reranker=get_reranker(),
            live=request.app.state.live,  # always live: top pages re-checked per question
            max_tokens=req.max_tokens,
            source_ids=source_ids,
            user_id=user.id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
