"""arq worker: crawls, the embedding queue, and model-endpoint health.

- crawl_site_task     enqueued by the API (add site / "Crawl now")
- embed_pending_task  every 30 s: embeds chunks waiting for a vector, if the GPU is up
- refresh_endpoint_health  every 30 s: records GPU model status for the UI
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import func, select, update

from app.api.health import HEALTH_CACHE_KEY
from app.config import get_settings
from app.crawler.crawler import crawl_site
from app.db.models import CrawlJob, Page, Site
from app.db.session import SessionLocal
from app.indexing.embedder import embed_pending
from app.llm.embed import EmbedClient
from app.llm.health import check_chat, check_embed, check_rerank

log = structlog.get_logger()


async def refresh_endpoint_health(ctx: dict) -> dict:
    s = get_settings()
    results = await asyncio.gather(check_chat(s), check_embed(s), check_rerank(s))
    snapshot = {
        "checked_at": datetime.now(UTC).isoformat(),
        "services": {r.name: r.dict() for r in results},
    }
    prev = ctx.get("prev_ok", {})
    for r in results:
        if prev.get(r.name) != r.ok:
            log.info("endpoint_state", service=r.name, ok=r.ok, detail=r.detail)
    ctx["prev_ok"] = {r.name: r.ok for r in results}
    ctx["embed_ok"] = next(r.ok for r in results if r.name == "embedding")
    await ctx["redis"].set(HEALTH_CACHE_KEY, json.dumps(snapshot), ex=300)
    return snapshot


async def crawl_site_task(ctx: dict, site_id: int, job_id: int) -> dict | None:
    stats = await crawl_site(site_id, job_id, get_settings(), SessionLocal)
    if stats is None:
        return None
    return {"seen": stats.seen, "changed": stats.changed, "unchanged": stats.unchanged,
            "removed": stats.removed, "failed": stats.failed}


async def embed_pending_task(ctx: dict) -> int:
    if ctx.get("embed_ok") is False:
        return 0  # GPU off: chunks wait; checked again on the next run
    s = get_settings()
    try:
        n = await embed_pending(SessionLocal, ctx["embedder"], batch_size=s.embed_batch_size)
    except (httpx.HTTPError, OSError) as exc:
        log.warning("embed_failed", error=f"{type(exc).__name__}: {exc}")
        return 0
    if n:
        log.info("embedded_chunks", count=n)
    return n


async def schedule_crawls(ctx: dict) -> None:
    """Persist due jobs first, then deliver all pending jobs idempotently to Redis."""
    async with SessionLocal() as db, db.begin():
        sites = await db.scalars(select(Site).where(Site.enabled).order_by(Site.id)
                                 .with_for_update(skip_locked=True))
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        for site in sites:
            last = await db.scalar(select(CrawlJob).where(CrawlJob.site_id == site.id)
                                   .order_by(CrawlJob.id.desc()).limit(1))
            if last and last.status in ("queued", "running"):
                continue
            due = (last.finished_at or last.created_at) if last else site.created_at
            # A URL retired by live verification after the last crawl signals a layout change.
            moved = await db.scalar(select(func.max(Page.verified_at)).where(
                Page.site_id == site.id, Page.status == "gone"))
            if due <= cutoff or (moved and moved > due):
                db.add(CrawlJob(site_id=site.id, status="queued", type="full", errors=[]))
    async with SessionLocal() as db:
        queued = (await db.execute(select(CrawlJob.site_id, CrawlJob.id)
                                   .where(CrawlJob.status == "queued"))).all()
    for site_id, job_id in queued:
        await ctx["redis"].enqueue_job("crawl_site_task", site_id, job_id,
                                       _job_id=f"crawl:{job_id}")


async def startup(ctx: dict) -> None:
    ctx["embedder"] = EmbedClient(get_settings())
    # A crawl that was running when the worker stopped can't resume: mark it failed.
    async with SessionLocal() as db, db.begin():
        await db.execute(
            update(CrawlJob).where(CrawlJob.status == "running").values(
                status="failed", finished_at=datetime.now(UTC),
                errors=[{"url": "", "error": "worker restarted during the crawl"}],
            )
        )
    log.info("worker_started")


async def shutdown(ctx: dict) -> None:
    await ctx["embedder"].aclose()


class WorkerSettings:
    functions = [crawl_site_task, embed_pending_task, refresh_endpoint_health]
    cron_jobs = [
        cron(schedule_crawls, second=5, run_at_startup=True, unique=True),
        cron(refresh_endpoint_health, second={0, 30}, run_at_startup=True, unique=True),
        cron(embed_pending_task, second={15, 45}, unique=True, timeout=120),
    ]
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 3600  # a full crawl can take a while (polite delays)
    max_jobs = 4
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
