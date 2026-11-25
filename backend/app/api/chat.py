"""Direct test chat with the chat model, streamed as Server-Sent Events.

Not grounded in any website yet (that is the Phase 1 orchestrator). Uses the same SSE event
names as the final design (`token`, `done`, `error`) so the frontend can be reused.
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openai import APIConnectionError, APIStatusError
from pydantic import BaseModel, Field, model_validator

from app.llm.chat import ChatTarget, get_chat

router = APIRouter(tags=["chat"])

# Plain assistant prompt for the free test chat. The strict "answer only from sources" rules belong
# to the grounded site-answering prompt (Phase 1); putting them here made the small model refuse
# ordinary requests such as writing an essay.
SYSTEM_PROMPT = (
    "You are Nullish, a helpful assistant. Do what the user asks. If you don't know a specific "
    "fact, say so rather than inventing it."
)
# ~6k tokens of history: well inside the 16k context of the small test model, leaving room for
# the answer. Oldest turns are dropped first.
MAX_HISTORY_CHARS = 24_000


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)
    temperature: float = Field(0.7, ge=0, le=2)
    # 1024 cut off long answers (e.g. essays); 2048 still leaves ample room in the 16k context.
    max_tokens: int = Field(2048, ge=1, le=8192)

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


async def stream_answer(target: ChatTarget, req: ChatRequest) -> AsyncIterator[str]:
    start = time.perf_counter()
    first_token_ms: float | None = None
    tokens = 0
    finish_reason: str | None = None
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + [
        m.model_dump() for m in trim_history(req.messages)
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
                        first_token_ms = round((time.perf_counter() - start) * 1000)
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
    req: ChatRequest, target: Annotated[ChatTarget, Depends(get_chat)]
) -> StreamingResponse:
    return StreamingResponse(
        stream_answer(target, req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
