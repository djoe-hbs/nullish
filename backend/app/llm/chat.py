"""OpenAI-compatible chat clients per role (served by vLLM on the rented GPU)."""

from dataclasses import dataclass
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import Settings, get_settings


@dataclass
class ChatTarget:
    client: AsyncOpenAI
    model: str


def chat_target(s: Settings) -> ChatTarget:
    return ChatTarget(
        AsyncOpenAI(base_url=s.chat_base_url, api_key=s.gpu_api_key, timeout=60, max_retries=0),
        s.chat_model,
    )


@lru_cache
def get_chat() -> ChatTarget:
    """Process-wide chat client: keeps its HTTP connections to the GPU open between requests
    (each new connection costs a full round trip over the SSH tunnel to the rented server)."""
    return chat_target(get_settings())


def router_target(s: Settings) -> ChatTarget:
    """Router shares the chat model unless a separate endpoint is configured (saves VRAM)."""
    if not s.router_base_url:
        t = chat_target(s)
        return ChatTarget(t.client, s.router_model or t.model)
    return ChatTarget(
        AsyncOpenAI(base_url=s.router_base_url, api_key=s.gpu_api_key, timeout=30, max_retries=0),
        s.router_model or s.chat_model,
    )


async def complete(target: ChatTarget, prompt: str, *, max_tokens: int = 64) -> str:
    resp = await target.client.chat.completions.create(
        model=target.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0,
    )
    return resp.choices[0].message.content or ""
