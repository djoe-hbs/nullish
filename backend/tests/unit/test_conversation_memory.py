import json
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI

from app.api.chat import ChatMessage, ChatRequest, stream_answer
from app.llm.chat import ChatTarget
from app.orchestrator.answer import chat_reply, history_of
from app.orchestrator.memory import (
    TurnRecord,
    budgeted_history,
    compact_stateless_messages,
    split_for_compaction,
    summarize_turns,
)
from app.orchestrator.router import route_message

BASE = "http://gpu:8001/v1"


class RecordingGPU:
    def __init__(self, responder):
        self.responder = responder
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append(payload)
        return self.responder(payload)

    def target(self) -> ChatTarget:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return ChatTarget(
            AsyncOpenAI(base_url=BASE, api_key="k", max_retries=0, http_client=client),
            "nullish-chat",
        )


def _json_completion(text: str) -> httpx.Response:
    choice = {
        "index": 0,
        "message": {"role": "assistant", "content": text},
        "finish_reason": "stop",
    }
    body = {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "nullish-chat",
        "choices": [choice],
    }
    return httpx.Response(200, json=body)


def _sse_completion(text: str) -> httpx.Response:
    c1 = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "nullish-chat",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    c2 = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "nullish-chat",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    stream_text = f"data: {json.dumps(c1)}\n\ndata: {json.dumps(c2)}\n\ndata: [DONE]\n\n"
    return httpx.Response(200, text=stream_text, headers={"content-type": "text/event-stream"})


def test_split_for_compaction_keeps_clean_user_boundary():
    prior = [
        TurnRecord(id=1, role="user", content="U1 " + "x" * 100),
        TurnRecord(id=2, role="assistant", content="A1 " + "y" * 100),
        TurnRecord(id=3, role="user", content="U2 " + "z" * 100),
        TurnRecord(id=4, role="assistant", content="A2 " + "w" * 100),
        TurnRecord(id=5, role="user", content="U3 " + "p" * 100),
        TurnRecord(id=6, role="assistant", content="A3 " + "q" * 100),
    ]
    # Remaining budget after current = 360 fits 3 turns (~309 chars) -> starts on A2
    # (assistant), so A2 moves into evicted and kept begins cleanly on U3 (user).
    evicted, kept = split_for_compaction(prior, "current question", char_budget=376)
    assert [r.id for r in evicted] == [1, 2, 3, 4]
    assert [r.id for r in kept] == [5, 6]
    assert kept[0].role == "user"


def test_split_for_compaction_honors_max_recent_messages():
    prior = [
        TurnRecord(id=i, role="user" if i % 2 else "assistant", content=f"msg {i}")
        for i in range(1, 25)
    ]
    evicted, kept = split_for_compaction(
        prior, "latest", char_budget=50_000, max_recent_messages=12
    )
    assert len(kept) == 12
    assert len(evicted) == 12
    assert kept[0].id == 13


async def test_summarize_turns_merges_existing_memory_across_batches():
    prompts: list[str] = []

    def respond(payload: dict) -> httpx.Response:
        prompts.append(payload["messages"][1]["content"])
        return _json_completion(f"- Summary v{len(prompts)}: budget=50, prefers hiking shoes.")

    gpu = RecordingGPU(respond)
    evicted = [
        TurnRecord(id=i, role="user" if i % 2 else "assistant", content=f"Turn {i}: " + "a" * 1800)
        for i in range(1, 15)
    ]
    summary, last_id, ok = await summarize_turns(gpu.target(), "User name is Asha.", evicted)
    assert ok is True
    assert last_id == 14
    assert summary and "prefers hiking shoes" in summary
    assert len(prompts) >= 2
    assert "Previous summary:\nUser name is Asha." in prompts[0]
    assert "Previous summary:\n- Summary v1" in prompts[1]


async def test_stateless_compaction_fallback_when_gpu_offline():
    def offline(_payload: dict) -> httpx.Response:
        raise httpx.ConnectError("down")

    gpu = RecordingGPU(offline)
    msgs = [
        {"role": "user" if i % 2 else "assistant", "content": f"Message {i}: fact-{i}"}
        for i in range(1, 26)
    ]
    compacted = await compact_stateless_messages(
        msgs, target=gpu.target(), char_budget=300, max_recent_messages=8
    )
    assert compacted.summary is not None
    assert "fact-1" in compacted.summary or "Message" in compacted.summary
    assert len(compacted.turns) <= 9  # up to 8 prior + latest


async def test_stream_answer_injects_rolling_memory_into_system_prompt():
    gpu = RecordingGPU(lambda _p: _sse_completion("You mentioned red shoes."))
    req = ChatRequest(
        messages=[ChatMessage(role="user", content="What color did I ask for in turn 1?")]
    )
    events = [
        e
        async for e in stream_answer(
            gpu.target(),
            req,
            system_prompt="You are Smish.",
            max_history_chars=24_000,
            conversation_summary="- User name is Asha and prefers red running shoes.",
        )
    ]
    assert any("You mentioned red shoes." in e for e in events)
    sent_system = gpu.requests[0]["messages"][0]["content"]
    assert "You are Smish." in sent_system
    assert "Earlier conversation memory" in sent_system
    assert "prefers red running shoes" in sent_system


async def test_router_and_chat_reply_receive_summary_and_expanded_window():
    def respond(payload: dict) -> httpx.Response:
        if "Reply with JSON only" in payload["messages"][0]["content"]:
            return _json_completion('{"intent": "search", "query": "red trail shoes under $50"}')
        return _sse_completion("Hello Asha!")

    gpu = RecordingGPU(respond)
    sites = [SimpleNamespace(name="Shop", root_url="https://shop.test", description="Shoes")]
    msgs = [
        {"role": "user" if i % 2 else "assistant", "content": f"turn {i}"}
        for i in range(1, 24)
    ]
    assert len(history_of(msgs)) == 12
    assert len(budgeted_history(msgs, max_messages=8, char_budget=25)) <= 8

    route = await route_message(
        gpu.target(),
        msgs,
        sites,
        "fallback",
        summary="- User wants trail shoes under $50.",
    )
    assert route.intent == "search" and "under $50" in route.query
    router_user_msg = gpu.requests[0]["messages"][1]["content"]
    assert "Earlier conversation summary:\n- User wants trail shoes under $50." in router_user_msg
    assert "turn 23" in router_user_msg

    events = [
        e
        async for e in chat_reply(
            msgs,
            sites,
            chat=gpu.target(),
            start=0.0,
            summary="- User name is Asha.",
        )
    ]
    assert any("Hello Asha!" in e for e in events)
    chat_prompt_msgs = gpu.requests[1]["messages"]
    assert "User name is Asha." in chat_prompt_msgs[0]["content"]
    # Keeps expanded 12-message verbatim prior + 1 current message = 13 turns + system = 14
    assert len(chat_prompt_msgs) == 14
