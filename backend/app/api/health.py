import asyncio
import json
import time

from fastapi import APIRouter, Query
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import get_settings
from app.db.session import engine
from app.llm.embed import get_embedder
from app.llm.health import ServiceStatus, check_chat, check_embed, check_rerank
from app.llm.rerank import get_reranker

router = APIRouter(tags=["health"])

HEALTH_CACHE_KEY = "nullish:health:worker"
LOCAL_SERVICES = ("database", "redis")
GPU_SERVICES = ("chat", "embedding", "reranker")


async def check_db() -> ServiceStatus:
    start = time.perf_counter()
    try:
        async with engine.connect() as conn:
            ext = await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ms = round((time.perf_counter() - start) * 1000, 1)
        if ext is None:
            return ServiceStatus("database", False, ms, "pgvector extension missing")
        return ServiceStatus("database", True, ms, f"pgvector {ext}")
    except Exception as exc:  # noqa: BLE001 - report any DB failure as status
        return ServiceStatus("database", False, None, type(exc).__name__)


async def check_redis() -> ServiceStatus:
    start = time.perf_counter()
    try:
        r = Redis.from_url(get_settings().redis_url)
        try:
            await r.ping()
        finally:
            await r.aclose()
        return ServiceStatus("redis", True, round((time.perf_counter() - start) * 1000, 1))
    except Exception as exc:  # noqa: BLE001
        return ServiceStatus("redis", False, None, type(exc).__name__)


async def deep_checks() -> dict:
    """Real calls: embed one string and rerank two docs (GPU models)."""
    out: dict = {}
    try:
        vec = (await get_embedder().embed(["hello"]))[0]
        out["embedding_dim"] = len(vec)
    except Exception as exc:  # noqa: BLE001
        out["embedding_error"] = str(exc)
    try:
        res = await get_reranker().rerank(
            "What is the return policy?",
            ["Our store opens at 9am.", "Items can be returned within 30 days."],
        )
        out["rerank"] = [{"index": r.index, "score": round(r.score, 4)} for r in res]
    except Exception as exc:  # noqa: BLE001
        out["rerank_error"] = str(exc)
    return out


@router.get("/health")
async def health(deep: bool = Query(False, description="also run a real embed + rerank call")):
    s = get_settings()
    results = await asyncio.gather(
        check_db(), check_redis(), check_embed(s), check_rerank(s), check_chat(s)
    )
    services = {r.name: r.dict() for r in results}
    # Local core must be up. GPU services (chat, embedding, reranker) being offline is expected:
    # the GPU is switched off to save cost, and chat / embedding simply wait for it.
    core_ok = all(services[n]["ok"] for n in LOCAL_SERVICES)
    body: dict = {
        "status": "ok" if core_ok else "degraded",
        "gpu_available": all(services[n]["ok"] for n in GPU_SERVICES),
        "services": services,
    }
    if services["redis"]["ok"]:
        r = Redis.from_url(s.redis_url)
        try:
            raw = await r.get(HEALTH_CACHE_KEY)
            body["worker_last_check"] = json.loads(raw) if raw else None
        finally:
            await r.aclose()
    if deep:
        body["deep"] = await deep_checks()
    return body
