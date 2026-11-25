"""Live verification at question time ("index to find, live to verify").

For every question:
  1. the top pages found by retrieval are re-checked against the live site, in parallel
     (cheap 304s for unchanged pages; changed pages are re-extracted and re-embedded at once;
     404s are dropped),
  2. pages the index doesn't have yet are discovered live from each site's sitemap (cached a few
     minutes) and on-site search page, and fetched too,
  3. anything that doesn't answer within the time budget is reported as "unverified".
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote_plus, urlsplit

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.crawler.browser import Browser
from app.crawler.extract import extract, looks_js_rendered
from app.crawler.fetcher import Fetcher
from app.crawler.pageproc import Outcome, PageProcessor, PrevPage, load_prev
from app.crawler.robots import Robots, load_robots
from app.crawler.sitemap import SitemapEntry, discover_sitemap_urls
from app.crawler.urls import canonicalize
from app.db.models import CrawlJob, Page, Site
from app.llm.embed import EmbedClient

log = structlog.get_logger()

STOPWORDS = {
    "the", "and", "for", "are", "was", "what", "which", "who", "whom", "whose", "when", "where",
    "why", "how", "does", "did", "do", "can", "could", "would", "should", "will", "with", "from",
    "about", "this", "that", "these", "those", "there", "their", "they", "them", "have", "has",
    "had", "any", "all", "you", "your", "our", "out", "into", "on", "in", "of", "to", "is", "it",
    "its", "a", "an", "or", "be", "by", "at", "as", "me", "my", "we", "us", "site", "page",
    "tell", "give", "show", "list", "find", "much", "many", "some", "more", "most", "get",
}


def terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3} - STOPWORDS


def _term_match(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def url_score(url: str, query_terms: set[str]) -> int:
    """How many query terms appear in the URL path (prefix match handles plurals etc.)."""
    path_terms = set(re.findall(r"[a-z0-9]+", urlsplit(url).path.lower()))
    return sum(1 for q in query_terms if any(_term_match(q, p) for p in path_terms))


@dataclass
class LiveResult:
    outcomes: dict[str, Outcome] = field(default_factory=dict)  # canonical URL -> outcome
    stats: dict = field(default_factory=dict)

    def by_page_id(self) -> dict[int, Outcome]:
        return {o.page_id: o for o in self.outcomes.values() if o.page_id is not None}


class LiveVerifier:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        fetcher: Fetcher,
        browser: Browser | None,
        embedder: EmbedClient | None,
    ):
        self.s = settings
        self.sessions = sessions
        self.fetcher = fetcher
        self.browser = browser
        self.embedder = embedder
        self._robots: dict[int, tuple[float, Robots]] = {}
        self._sitemaps: dict[int, tuple[float, list[SitemapEntry]]] = {}

    async def close(self) -> None:
        await self.fetcher.close()
        if self.browser is not None:
            await self.browser.close()

    # ---- cached per-site metadata --------------------------------------------------------

    async def robots_for(self, site: Site) -> Robots:
        if not site.respect_robots:
            return Robots(None)
        hit = self._robots.get(site.id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        robots = await load_robots(self.fetcher, canonicalize(site.root_url) or site.root_url)
        self._robots[site.id] = (time.monotonic() + self.s.robots_cache_sec, robots)
        return robots

    async def sitemap_for(self, site: Site, robots: Robots) -> list[SitemapEntry]:
        hit = self._sitemaps.get(site.id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        entries = await discover_sitemap_urls(
            self.fetcher, canonicalize(site.root_url) or site.root_url, robots.sitemaps,
            limit=5000,
        )
        self._sitemaps[site.id] = (time.monotonic() + self.s.sitemap_cache_sec, entries)
        return entries

    # ---- discovery -----------------------------------------------------------------------

    async def discover(self, site: Site, proc: PageProcessor, robots: Robots, query: str,
                       known: set[str]) -> list[str]:
        """Up to N URLs for this site that look relevant and aren't being checked already."""
        q = terms(query)
        found: list[str] = []

        if site.site_search_url and "{query}" in site.site_search_url:
            search_url = site.site_search_url.replace("{query}", quote_plus(query))
            try:
                res = await self.fetcher.fetch(search_url)
                if res.status == 200 and res.is_html:
                    html = res.text()
                    ex = extract(html, res.url)
                    if looks_js_rendered(html, ex) and proc.browser is not None:
                        html, _, location = await proc.browser.render(res.url)
                        ex = extract(html, location)
                    search_key = canonicalize(search_url)
                    for href in ex.links:
                        c = canonicalize(href, res.url)
                        if (c and c != search_key and proc.scoped(c) and c not in known
                                and c not in found and url_score(c, q) > 0):
                            found.append(c)
            except Exception as exc:  # noqa: BLE001 - discovery is best-effort
                log.info("site_search_failed", site=site.root_url, error=str(exc))

        try:
            entries = await self.sitemap_for(site, robots)
        except Exception as exc:  # noqa: BLE001
            log.info("sitemap_failed", site=site.root_url, error=str(exc))
            entries = []
        scored = []
        for e in entries:
            c = canonicalize(e.loc)
            if c and c not in known and c not in found and proc.scoped(c):
                score = url_score(c, q)
                if score > 0:
                    scored.append((score, e.lastmod or "", c))
        scored.sort(reverse=True)  # most matching terms first, then most recently modified
        found.extend(c for _, _, c in scored)

        if not found:
            return []
        # Prefer pages the index doesn't have at all (they are what discovery is for).
        async with self.sessions() as db:
            indexed = set(await db.scalars(
                select(Page.canonical_url).where(Page.site_id == site.id,
                                                 Page.canonical_url.in_(found))
            ))
        fresh = [u for u in found if u not in indexed]
        rest = [u for u in found if u in indexed]
        return (fresh + rest)[: self.s.live_discovery_per_site]

    # ---- verification --------------------------------------------------------------------

    async def verify(
        self, sites: list[Site], pages: list[tuple[int, int, str]], query: str
    ) -> LiveResult:
        """`pages`: (page_id, site_id, canonical_url) of the top retrieved pages, best first."""
        t0 = time.perf_counter()
        result = LiveResult()
        by_site = {s.id: s for s in sites}
        procs = {
            s.id: PageProcessor(
                s, self.s, self.sessions, self.fetcher, self.browser, embedder=self.embedder,
                render_timeout_ms=int(self.s.live_render_timeout_sec * 1000),
            )
            for s in sites
        }
        robots_list = await asyncio.gather(*(self.robots_for(s) for s in sites))
        robots = dict(zip(by_site, robots_list, strict=True))

        prev: dict[str, PrevPage] = {}
        async with self.sessions() as db:
            for sid in {sid for _, sid, _ in pages}:
                ids = [pid for pid, s, _ in pages if s == sid]
                prev.update(await load_prev(db, sid, page_ids=ids))

        known = {url for _, _, url in pages}
        discovered = await asyncio.gather(
            *(self.discover(s, procs[s.id], robots[s.id], query, known) for s in sites),
            return_exceptions=True,
        )
        targets: list[tuple[int, str, PrevPage | None, bool]] = [
            (sid, url, prev.get(url), False) for _, sid, url in pages if sid in procs
        ]
        n_discovered = 0
        async with self.sessions() as db:
            for s, urls in zip(sites, discovered, strict=True):
                if not isinstance(urls, list) or not urls:
                    continue
                # Discovered URLs may already be indexed: use their stored state too (re-use
                # window, conditional GET, "needs a browser").
                known_prev = await load_prev(db, s.id, urls=urls)
                for u in urls:
                    targets.append((s.id, u, known_prev.get(u), True))
                    n_discovered += 1

        sems = {sid: asyncio.Semaphore(self.s.live_per_site_concurrency) for sid in procs}
        claimed: dict[int, set[str]] = {sid: set() for sid in procs}

        window = self.s.live_reverify_after_sec
        now = datetime.now(UTC)

        async def one(sid: int, url: str, p: PrevPage | None) -> Outcome:
            if not robots[sid].allowed(url):
                return Outcome(url, "skipped", p.page_id if p else None, error="robots.txt")
            recently = (
                p is not None and window > 0 and p.verified_at is not None
                and (now - p.verified_at).total_seconds() < window
            )
            if recently:
                return Outcome(url, "unchanged", p.page_id, p.links, recent=True,
                               verified_at=p.verified_at)
            async with sems[sid]:
                try:
                    return await procs[sid].process(url, p, claimed=claimed[sid])
                except Exception as exc:  # noqa: BLE001 - timeouts, resets, TLS errors...
                    return Outcome(url, "error", p.page_id if p else None,
                                   error=f"{type(exc).__name__}: {exc}"[:200])

        tasks = {asyncio.create_task(one(sid, url, p)): (url, p, disc)
                 for sid, url, p, disc in targets}
        done, pending = await asyncio.wait(tasks, timeout=self.s.live_total_budget_sec) \
            if tasks else (set(), set())
        for t in pending:
            t.cancel()
        for t, (url, p, disc) in tasks.items():
            if t in done and not t.cancelled() and t.exception() is None:
                out = t.result()
            else:
                out = Outcome(url, "error", p.page_id if p else None, error="timed out")
            out.discovered = disc
            if p and out.url != url and out.status in ("changed", "new", "unchanged"):
                result.outcomes[out.url] = out
                result.outcomes[url] = Outcome(url, "gone", p.page_id)
                out.discovered = True
            else:
                result.outcomes[url] = out

        # A redesigned page may still return 200. Rediscover from the root if at least
        # half a substantial set of old navigation links disappeared in this live check.
        redesigns = set()
        for sid, url, prior, _ in targets:
            outcome = result.outcomes.get(url)
            if prior and outcome and not outcome.recent and outcome.http_status == 200:
                old = set(prior.links)
                if len(old) >= 4 and len(old - set(outcome.links)) * 2 >= len(old):
                    redesigns.add(sid)
        if redesigns:
            async with self.sessions() as db, db.begin():
                for sid in sorted(redesigns):
                    site = await db.get(Site, sid, with_for_update=True)
                    active = await db.scalar(select(CrawlJob.id).where(
                        CrawlJob.site_id == sid, CrawlJob.status.in_(("queued", "running"))))
                    if site and site.enabled and active is None:
                        db.add(CrawlJob(site_id=sid, type="full", status="queued", errors=[]))
                    self._sitemaps.pop(sid, None)

        counts: dict[str, int] = {}
        for o in result.outcomes.values():
            key = "recently_verified" if o.recent else o.status
            counts[key] = counts.get(key, 0) + 1
        result.stats = {
            "checked": len(targets) - n_discovered,
            "discovered": n_discovered,
            **counts,
            "live_ms": round((time.perf_counter() - t0) * 1000),
        }
        return result
