"""Grounded Q&A over the configured sites.

Streamed as SSE events: status, sources, token, then done (or error).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.api.chat import ChatMessage, ChatRequest
from app.config import get_settings
from app.db.session import SessionLocal
from app.llm.chat import ChatTarget, get_chat
from app.llm.embed import get_embedder
from app.llm.rerank import get_reranker
from app.orchestrator.answer import answer_stream

router = APIRouter(tags=["ask"])


class AskRequest(ChatRequest):
    """Inherits ChatRequest's validation (the last message must be the user's question)."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    site_ids: list[int] | None = Field(None, max_length=200)  # None/empty = all enabled sites
    max_tokens: int = Field(1024, ge=1, le=4096)


@router.post("/ask")
async def ask(
    req: AskRequest, request: Request, chat: Annotated[ChatTarget, Depends(get_chat)]
) -> StreamingResponse:
    return StreamingResponse(
        answer_stream(
            [m.model_dump() for m in req.messages],
            req.site_ids or None,
            settings=get_settings(),
            sessions=SessionLocal,
            chat=chat,
            embedder=get_embedder(),
            reranker=get_reranker(),
            live=request.app.state.live,  # always live: top pages re-checked per question
            max_tokens=req.max_tokens,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
