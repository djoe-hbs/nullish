"""Endpoint health checks. An offline GPU (switched off to save cost) is a normal state.

All three GPU servers are vLLM, so each is probed with GET /v1/models. That also reveals the
real model behind the served alias ("root"), which is how the status page shows whether the
GPU is running the small test model or the full-size one.
"""

import time
from dataclasses import asdict, dataclass

import httpx

from app.config import Settings


@dataclass
class ServiceStatus:
    name: str
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None
    model: str | None = None

    def dict(self) -> dict:
        return asdict(self)


def served_root(payload: dict, alias: str) -> str | None:
    """Real model id behind a vLLM served alias (falls back to the alias itself)."""
    for m in payload.get("data", []):
        if m.get("id") == alias:
            return m.get("root") or alias
    return None


async def check_vllm(
    name: str,
    base_url: str,
    alias: str,
    *,
    api_key: str,
    timeout_sec: float,
    expected_root: str | None = None,
) -> ServiceStatus:
    url = f"{base_url.rstrip('/').removesuffix('/v1')}/v1/models"
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        return ServiceStatus(name, False, None, f"offline ({type(exc).__name__})", alias)
    latency = round((time.perf_counter() - start) * 1000, 1)
    if resp.status_code >= 400:
        return ServiceStatus(name, False, latency, f"HTTP {resp.status_code}", alias)
    try:
        root = served_root(resp.json(), alias)
    except ValueError:
        return ServiceStatus(name, False, latency, "invalid /v1/models response", alias)
    if root is None:
        return ServiceStatus(name, False, latency, f"model '{alias}' not served", alias)
    if expected_root and root != expected_root:
        return ServiceStatus(
            name, False, latency, f"serves {root}, index expects {expected_root}", root
        )
    return ServiceStatus(name, True, latency, None, root)


async def check_chat(s: Settings) -> ServiceStatus:
    return await check_vllm(
        "chat", s.chat_base_url, s.chat_model,
        api_key=s.gpu_api_key, timeout_sec=s.health_timeout_sec,
    )


async def check_embed(s: Settings) -> ServiceStatus:
    return await check_vllm(
        "embedding", s.embed_url, s.embed_model,
        api_key=s.gpu_api_key, timeout_sec=s.health_timeout_sec,
        expected_root=s.embed_model_id,
    )


async def check_rerank(s: Settings) -> ServiceStatus:
    return await check_vllm(
        "reranker", s.rerank_url, s.rerank_model,
        api_key=s.gpu_api_key, timeout_sec=s.health_timeout_sec,
    )
