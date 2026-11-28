"""Direct test chat with the chat model, streamed as Server-Sent Events."""

import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openai import APIConnectionError, APIStatusError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.conversations import persist_sse_stream, prepare_conversation_turn
from app.auth.deps import current_user
from app.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal, get_session
from app.llm.chat import ChatTarget, get_chat
from app.orchestrator.memory import (
    append_memory_sse,
    compact_prepared_conversation,
    compact_stateless_messages,
    format_memory_block,
)
from app.settings_store import DEFAULT_CHAT_PROMPT, get_system_settings

router = APIRouter(tags=["chat"])

SYSTEM_PROMPT = DEFAULT_CHAT_PROMPT
MAX_HISTORY_CHARS = 24_000


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    conversation_id: str | None = Field(None, min_length=8, max_length=36)
    conversation_summary: str | None = Field(None, max_length=4_000)
    selected_sources: list[int] | None = Field(None, max_length=200)
    temperature: float = Field(0.7, ge=0, le=2)
    # None = no limit: sent to vLLM as null, so the model writes until it finishes or the context
    # window (CHAT_MAX_LEN) is full. (vLLM rejects -1.) A client may still ask for a cap.
    max_tokens: int | None = Field(None, ge=1, le=8192)

    @model_validator(mode="after")
    def last_is_user(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("the last message must be from the user")
        return self


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def trim_history(messages: list[ChatMessage], budget: int = MAX_HISTORY_CHARS) -> list[ChatMessage]:
    """Keep the newest messages within the budget; the latest message is always kept."""
    kept: list[ChatMessage] = []
    total = 0
    for m in reversed(messages):
        total += len(m.content)
        if kept and total > budget:
            break
        kept.append(m)
    return list(reversed(kept))


async def stream_answer(
    target: ChatTarget,
    req: ChatRequest,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    max_history_chars: int = MAX_HISTORY_CHARS,
    conversation_summary: str | None = None,
) -> AsyncIterator[str]:
    start = time.perf_counter()
    first_token_ms: float | None = None
    tokens = 0
    finish_reason: str | None = None
    mem_block = format_memory_block(conversation_summary, grounded_mode=False)
    sys_content = f"{system_prompt}\n\n{mem_block}" if mem_block else system_prompt
    kept_budget = (
        max(min(max_history_chars, 1_200), max_history_chars - len(mem_block) - 4)
        if mem_block
        else max_history_chars
    )
    messages = [{"role": "system", "content": sys_content}] + [
        m.model_dump() for m in trim_history(req.messages, budget=kept_budget)
    ]
    try:
        stream = await target.client.chat.completions.create(
            model=target.model,
            messages=messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=True,
        )
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    if first_token_ms is None:
                        out_ft = round((time.perf_counter() - start) * 1000)
                        first_token_ms = out_ft
                    tokens += 1
                    yield sse("token", {"t": choice.delta.content})
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        finally:
            # Also runs when the browser disconnects (Stop button): vLLM aborts the generation.
            await stream.close()
    except APIConnectionError:
        yield sse("error", {"message": "Chat model is offline. Start the GPU and gpu/tunnel.ps1."})
        return
    except APIStatusError as exc:
        yield sse("error", {"message": f"Model server returned HTTP {exc.status_code}."})
        return
    yield sse(
        "done",
        {
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "first_token_ms": first_token_ms,
            "tokens": tokens,
            "finish_reason": finish_reason,
        },
    )


@router.post("/chat")
async def chat(
    req: ChatRequest,
    target: Annotated[ChatTarget, Depends(get_chat)],
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    sys_cfg = await get_system_settings(db, get_settings())
    persist = bool(req.conversation_id) and user.storage_mode != "local"
    if persist and req.conversation_id:
        prepared = await prepare_conversation_turn(
            db,
            user_id=user.id,
            conversation_id=req.conversation_id,
            user_text=req.messages[-1].content,
            mode="model",
            selected_sources=req.selected_sources,
        )
        compacted = await compact_prepared_conversation(
            prepared,
            target=target,
            db=db,
            char_budget=sys_cfg.max_history_chars,
        )
    else:
        compacted = await compact_stateless_messages(
            [m.model_dump() for m in req.messages],
            target=target,
            char_budget=sys_cfg.max_history_chars,
            existing_summary=req.conversation_summary,
        )
    effective_req = req.model_copy(
        update={
            "messages": [
                ChatMessage(
                    role="user" if t["role"] == "user" else "assistant",
                    content=t["content"],
                )
                for t in compacted.turns
            ]
        }
    )

    stream = stream_answer(
        target,
        effective_req,
        system_prompt=sys_cfg.chat_system_prompt,
        max_history_chars=sys_cfg.max_history_chars,
        conversation_summary=compacted.summary,
    )
    if persist and req.conversation_id:
        stream = persist_sse_stream(
            stream,
            sessions=SessionLocal,
            conversation_id=req.conversation_id,
            mode="model",
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
