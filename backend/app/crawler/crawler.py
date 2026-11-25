"""Background site crawl: robots.txt -> sitemap + root seeds -> breadth-first, in-scope links.

Per-page freshness rules live in pageproc.PageProcessor (shared with live verification).
New chunks have no embedding yet; the embed worker fills them in whenever the GPU is up.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import islice

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.crawler.browser import Browser
from app.crawler.fetcher import Fetcher
from app.crawler.pageproc import PageProcessor, PrevPage, load_prev
from app.crawler.robots import Robots, load_robots
from app.crawler.sitemap import discover_sitemap_urls
from app.crawler.urls import canonicalize
from app.db.models import CrawlJob, Site

log = structlog.get_logger()

MAX_ERRORS_STORED = 50


@dataclass
class Stats:
    seen: int = 0
    changed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)

    def error(self, url: str, msg: str) -> None:
        self.failed += 1
        if len(self.errors) < MAX_ERRORS_STORED:
            self.errors.append({"url": url, "error": msg[:300]})


def now() -> datetime:
    return datetime.now(UTC)


class SiteCrawler:
    def __init__(
        self,
        site: Site,
        job_id: int,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        fetcher: Fetcher,
        browser: Browser | None = None,
    ):
        self.site = site
        self.job_id = job_id
        self.s = settings
        self.sessions = session_factory
        self.proc = PageProcessor(site, settings, session_factory, fetcher, browser)
        self.fetcher = fetcher
        self.root = self.proc.root
        self.stats = Stats()
        self.robots = Robots(None)
        self.queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self.enqueued: set[str] = set()
        self.claimed: set[str] = set()  # canonical URLs already stored this run
        self.prev: dict[str, PrevPage] = {}
        self.delay = site.crawl_delay_ms / 1000

    def enqueue(self, url: str | None, depth: int) -> None:
        if not url or url in self.enqueued or url in self.claimed:
            return
        if len(self.enqueued) >= self.site.max_pages:
            return
        if not self.proc.scoped(url) and url != self.root:
            return
        self.enqueued.add(url)
        self.queue.put_nowait((url, depth))

    async def flush_job(self, status: str | None = None) -> None:
        async with self.sessions() as db, db.begin():
            job = await db.get(CrawlJob, self.job_id)
            if job is None:
                return
            job.pages_seen = self.stats.seen
            job.pages_changed = self.stats.changed
            job.pages_unchanged = self.stats.unchanged
            job.pages_removed = self.stats.removed
            job.pages_failed = self.stats.failed
            job.errors = self.stats.errors
            if status:
                job.status = status
                job.finished_at = now()

    async def process(self, url: str, depth: int) -> None:
        if self.site.respect_robots and not self.robots.allowed(url):
            return
        self.stats.seen += 1
        out = await self.proc.process(url, self.prev.get(url), claimed=self.claimed)
        if out.status in ("changed", "new"):
            self.stats.changed += 1
        elif out.status == "unchanged":
            self.stats.unchanged += 1
        elif out.status == "gone":
            self.stats.removed += 1
        elif out.status == "error":
            self.stats.error(url, out.error or "error")
        if out.links and depth < self.site.max_depth:
            for link in out.links:
                self.enqueue(link, depth + 1)

    async def worker(self) -> None:
        while True:
            url, depth = await self.queue.get()
            try:
                await self.process(url, depth)
            except Exception as exc:  # noqa: BLE001 - one bad page must not stop the crawl
                self.stats.error(url, f"{type(exc).__name__}: {exc}")
            finally:
                self.queue.task_done()
            if self.stats.seen % 10 == 0:
                await self.flush_job()
            await asyncio.sleep(self.delay)

    async def run(self) -> Stats:
        if self.site.respect_robots:
            self.robots = await load_robots(self.fetcher, self.root)
            if self.robots.crawl_delay:
                self.delay = max(self.delay, min(self.robots.crawl_delay, 10.0))
        async with self.sessions() as db:
            self.prev = await load_prev(db, self.site.id)

        self.enqueue(self.root, 0)
        for entry in await discover_sitemap_urls(
            self.fetcher, self.root, self.robots.sitemaps, limit=self.site.max_pages * 2
        ):
            self.enqueue(canonicalize(entry.loc), 1)

        workers = [asyncio.create_task(self.worker()) for _ in range(self.s.crawl_concurrency)]
        try:
            await self.queue.join()
            # Audit old URLs absent from current navigation. Oldest first rotates the
            # bounded audit even on sites that fill the discovery budget every hour.
            old = sorted(self.prev, key=lambda u: self.prev[u].verified_at
                         or datetime.min.replace(tzinfo=UTC))
            missing = (u for u in old if u not in self.enqueued and u not in self.claimed
                       and self.proc.scoped(u))
            for url in islice(missing, min(25, self.site.max_pages)):
                self.queue.put_nowait((url, self.site.max_depth))
            await self.queue.join()
        finally:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        return self.stats


async def crawl_site(
    site_id: int,
    job_id: int,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    use_browser: bool = True,
) -> Stats | None:
    async with session_factory() as db, db.begin():
        site = await db.get(Site, site_id)
        job = await db.get(CrawlJob, job_id, with_for_update=True)
        if site is None or job is None or job.status != "queued":
            return None
        job.status = "running"
        job.started_at = now()
        db.expunge(site)

    fetcher = Fetcher(
        settings.crawler_user_agent,
        timeout=settings.crawl_fetch_timeout_sec,
        max_bytes=settings.max_page_bytes,
        allow_private=settings.allow_private_urls,
    )
    browser = (
        Browser(settings.crawler_user_agent, allow_private=settings.allow_private_urls)
        if use_browser and site.render_mode != "static" else None
    )
    crawler = SiteCrawler(site, job_id, settings, session_factory, fetcher, browser)
    try:
        stats = await crawler.run()
        await crawler.flush_job("done")
        log.info("crawl_done", site=site.root_url, seen=stats.seen, changed=stats.changed,
                 unchanged=stats.unchanged, removed=stats.removed, failed=stats.failed)
        return stats
    except asyncio.CancelledError:
        crawler.stats.errors.append({"url": site.root_url, "error": "crawl interrupted"})
        await crawler.flush_job("failed")
        raise
    except Exception as exc:
        crawler.stats.errors.append({"url": site.root_url, "error": f"crawl aborted: {exc}"[:300]})
        await crawler.flush_job("failed")
        raise
    finally:
        await fetcher.close()
        if browser is not None:
            await browser.close()
