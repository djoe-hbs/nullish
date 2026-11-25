import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.api.chat import ChatMessage, ChatRequest, stream_answer, trim_history
from app.llm.chat import ChatTarget

BASE = "http://gpu:8001/v1"


class FakeGPU:
    """Explicit mock transport (the openai SDK's own transport is not intercepted by respx)."""

    def __init__(self, response: httpx.Response | None = None, error: Exception | None = None):
        self.response, self.error, self.requests = response, error, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        assert request.url == f"{BASE}/chat/completions"
        return self.response

    def target(self) -> ChatTarget:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return ChatTarget(
            AsyncOpenAI(base_url=BASE, api_key="k", max_retries=0, http_client=client),
            "nullish-chat",
        )


def sse_chunk(content: str | None, finish: str | None = None) -> str:
    delta = {"content": content} if content is not None else {}
    body = {
        "id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "nullish-chat",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n"


def parse(events: list[str]) -> list[tuple[str, dict]]:
    out = []
    for e in events:
        lines = e.strip().split("\n")
        out.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return out


def req(*msgs: tuple[str, str]) -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role=r, content=c) for r, c in msgs])


async def test_streams_tokens_then_done():
    body = sse_chunk("Hel") + sse_chunk("lo") + sse_chunk(None, "stop") + "data: [DONE]\n\n"
    gpu = FakeGPU(httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    events = parse([e async for e in stream_answer(gpu.target(), req(("user", "hi")))])

    assert [e for e, _ in events] == ["token", "token", "done"]
    assert "".join(d["t"] for e, d in events if e == "token") == "Hello"
    done = events[-1][1]
    assert done["tokens"] == 2 and done["finish_reason"] == "stop"

    assert gpu.requests[0].headers["authorization"] == "Bearer k"
    sent = json.loads(gpu.requests[0].content)
    assert sent["stream"] is True and sent["model"] == "nullish-chat"
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][-1] == {"role": "user", "content": "hi"}


async def test_gpu_offline_yields_error_event():
    gpu = FakeGPU(error=httpx.ConnectError("down"))
    events = parse([e async for e in stream_answer(gpu.target(), req(("user", "hi")))])
    assert events == [("error", {"message": events[0][1]["message"]})]
    assert "offline" in events[0][1]["message"]


async def test_http_error_yields_error_event():
    gpu = FakeGPU(httpx.Response(401, json={}))
    events = parse([e async for e in stream_answer(gpu.target(), req(("user", "hi")))])
    assert events[0][0] == "error" and "401" in events[0][1]["message"]


def test_trim_history_keeps_newest_within_budget():
    msgs = [
        ChatMessage(role="user", content="a" * 10),
        ChatMessage(role="assistant", content="b" * 10),
        ChatMessage(role="user", content="c" * 10),
    ]
    assert [m.content[0] for m in trim_history(msgs, budget=25)] == ["b", "c"]
    # The latest message is kept even when it alone exceeds the budget.
    assert [m.content[0] for m in trim_history(msgs, budget=5)] == ["c"]


def test_request_must_end_with_user_message():
    with pytest.raises(ValidationError):
        req(("user", "hi"), ("assistant", "hello"))
    with pytest.raises(ValidationError):
        ChatRequest(messages=[])
