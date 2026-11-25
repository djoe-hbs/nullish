"""Fetch -> extract -> version -> store, for ONE page.

Shared by the background crawler and by live verification at question time, so both follow
exactly the same freshness rules:
  304 Not Modified       -> unchanged (content kept, verified_at refreshed)
  404 / 410              -> gone: chunks deleted, never retrievable again
  200, same text hash    -> unchanged (markup/ads churn ignored)
  200, new text          -> version + 1; old chunks replaced by new ones in ONE transaction
JavaScript-rendered pages (stored with meta.rendered) are always re-rendered: their static HTML
is a shell that is identical for every URL, so a 304 on it says nothing about the content.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.crawler.browser import Browser, BrowserUnavailable
from app.crawler.extract import Extracted, content_hash, extract, looks_js_rendered
from app.crawler.fetcher import Fetcher
from app.crawler.urls import canonicalize, in_scope
from app.db.models import Chunk, Page, Site
from app.indexing.chunker import chunk_markdown
from app.indexing.embedder import embed_page
from app.llm.embed import EmbedClient

log = structlog.get_logger()

MIN_TEXT_CHARS = 50  # pages with less extracted text are stored but not chunked


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class PrevPage:
    page_id: int
    etag: str | None
    last_modified: str | None
    content_hash: str | None
    links: list[str]
    rendered: bool = False
    verified_at: datetime | None = None


async def load_prev(
    db: AsyncSession, site_id: int, *, urls: list[str] | None = None,
    page_ids: list[int] | None = None,
) -> dict[str, PrevPage]:
    """Previous state of active pages, keyed by canonical URL."""
    q = select(Page.id, Page.canonical_url, Page.etag, Page.last_modified, Page.content_hash,
               Page.meta, Page.verified_at).where(Page.site_id == site_id,
                                                  Page.status == "active")
    if urls is not None:
        q = q.where(Page.canonical_url.in_(urls))
    if page_ids is not None:
        q = q.where(Page.id.in_(page_ids))
    out = {}
    for pid, url, etag, lm, h, meta, verified_at in await db.execute(q):
        meta = meta or {}
        out[url] = PrevPage(pid, etag, lm, h, meta.get("links", []), bool(meta.get("rendered")),
                            verified_at)
    return out


@dataclass
class Outcome:
    url: str  # canonical URL the page is stored under (or the requested URL)
    # unchanged | changed | new | gone | error | skipped
    status: str
    page_id: int | None = None
    links: list[str] = field(default_factory=list)
    http_status: int | None = None
    error: str | None = None
    rendered: bool = False
    discovered: bool = False  # found live (sitemap / site search), not via the index
    recent: bool = False  # verified live moments ago; not fetched again (see reverify window)
    verified_at: datetime | None = None


class PageProcessor:
    def __init__(
        self,
        site: Site,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        fetcher: Fetcher,
        browser: Browser | None = None,
        *,
        embedder: EmbedClient | None = None,  # set for live checks: embed changed text at once
        render_timeout_ms: int | None = None,
    ):
        self.site = site
        self.s = settings
        self.sessions = sessions
        self.fetcher = fetcher
        self.browser = browser if site.render_mode != "static" else None
        self.embedder = embedder
        self.render_timeout_ms = render_timeout_ms
        self.root = canonicalize(site.root_url) or site.root_url

    def scoped(self, url: str | None) -> bool:
        return bool(url) and in_scope(
            url, self.root, self.site.include_patterns or [], self.site.exclude_patterns or []
        )

    # ---- fetching ------------------------------------------------------------------------

    async def _render(self, url: str) -> tuple[str, int, str] | None:
        if self.browser is None:
            return None
        try:
            return await self.browser.render(url, timeout_ms=self.render_timeout_ms)
        except BrowserUnavailable:
            self.browser = None
        except Exception as exc:  # noqa: BLE001 - caller falls back / reports
            log.warning("browser_render_failed", url=url, error=str(exc))
        return None

    async def process(
        self, url: str, prev: PrevPage | None, *, claimed: set[str] | None = None
    ) -> Outcome:
        """`claimed`: canonical URLs already handled in this run (redirect/canonical dedupe)."""
        claimed = claimed if claimed is not None else set()
        rendered = False
        headers: dict[str, str] = {}

        if prev is not None and prev.rendered and self.browser is not None:
            # Known JavaScript page: the static shell tells us nothing, render straight away.
            got = await self._render(url)
            if got is None:
                return Outcome(url, "error", prev.page_id, error="render failed")
            html, status, location = got
            final, rendered = canonicalize(location) or url, True
            if final != url and status in (200, 404, 410):
                await self._gone(url, 301)
                if not self.scoped(final) or final in claimed:
                    return Outcome(url, "gone", prev.page_id)

            if status in (404, 410):
                return await self._gone(final, status)
            if status != 200:
                return Outcome(url, "error", prev.page_id, http_status=status,
                               error=f"HTTP {status}")
            ex = extract(html, final)
        else:
            res = await self.fetcher.fetch(
                url,
                etag=prev.etag if prev else None,
                last_modified=prev.last_modified if prev else None,
            )
            headers = res.headers
            final = canonicalize(res.url) or url
            if final != url and prev and res.status in (200, 404, 410):
                await self._gone(url, 301)
            if final != url and (not self.scoped(final) or final in claimed):
                if prev and res.status in (200, 404, 410):
                    return Outcome(url, "gone", prev.page_id)
                return Outcome(url, "skipped", http_status=res.status)
            if res.status == 304 and prev:
                pid = await self._save_unchanged(final, 304, headers)
                return Outcome(final, "unchanged", pid, prev.links, 304)
            if res.status in (404, 410):
                return await self._gone(final, res.status)
            if res.status != 200:
                return Outcome(url, "error", prev.page_id if prev else None,
                               http_status=res.status, error=f"HTTP {res.status}")
            if not res.is_html:
                return Outcome(url, "skipped", http_status=200)
            html = res.text()
            ex = extract(html, final)
            wants_browser = self.site.render_mode == "browser" or (
                self.site.render_mode == "auto" and looks_js_rendered(html, ex)
            )
            if wants_browser:
                got = await self._render(final)
                if got is not None and got[1] == 200:
                    rendered_url = canonicalize(got[2]) or final
                    if rendered_url != final:
                        if prev:
                            await self._gone(url, 301)
                        if not self.scoped(rendered_url):
                            return Outcome(url, "gone" if prev else "skipped",
                                           prev.page_id if prev else None)
                        final = rendered_url
                    ex, rendered = extract(got[0], final), True

        key = self._canonical_key(final, ex)
        if key in claimed and key != final:
            return Outcome(url, "skipped", http_status=200)  # same page under its canonical URL
        claimed.update({final, key})
        links = list(dict.fromkeys(
            c for c in (canonicalize(href, final) for href in ex.links) if c and self.scoped(c)
        ))
        status, pid = await self._save_content(key, final, headers, ex, links, rendered)
        if status in ("changed", "new") and self.embedder is not None and pid is not None:
            try:
                await embed_page(self.sessions, self.embedder, pid)
            except Exception as exc:  # noqa: BLE001 - the embed worker retries later
                log.warning("live_embed_failed", url=key, error=str(exc))
        return Outcome(key, status, pid, links, 200, rendered=rendered)

    def _canonical_key(self, final: str, ex: Extracted) -> str:
        if not ex.canonical:
            return final
        canon = canonicalize(ex.canonical, final)
        # A deep page whose canonical is the home page is almost always a misconfiguration
        # (typical of SPAs). Trusting it would fold every page of the site into one.
        points_home = canon == self.root and final != self.root
        if canon and canon != final and self.scoped(canon) and not points_home:
            return canon
        return final

    # ---- persistence ---------------------------------------------------------------------

    async def _get_page(self, db: AsyncSession, url: str) -> Page | None:
        return await db.scalar(
            select(Page).where(Page.site_id == self.site.id, Page.canonical_url == url)
        )

    async def _save_unchanged(self, url: str, http_status: int, headers: dict) -> int | None:
        async with self.sessions() as db, db.begin():
            page = await self._get_page(db, url)
            if page is None:
                return None
            page.verified_at = now()
            page.http_status = http_status
            page.status = "active"
            if headers.get("etag"):
                page.etag = headers["etag"]
            if headers.get("last-modified"):
                page.last_modified = headers["last-modified"]
            return page.id

    async def _gone(self, url: str, http_status: int) -> Outcome:
        async with self.sessions() as db, db.begin():
            page = await self._get_page(db, url)
            if page is None:
                return Outcome(url, "skipped", http_status=http_status)
            if page.status != "gone":
                page.status = "gone"
                page.http_status = http_status
                page.verified_at = page.fetched_at = now()
                await db.execute(delete(Chunk).where(Chunk.page_id == page.id))
            return Outcome(url, "gone", page.id, http_status=http_status)

    async def _save_content(
        self, url: str, fetched_url: str, headers: dict, ex: Extracted, links: list[str],
        rendered: bool,
    ) -> tuple[str, int]:
        h = content_hash(ex.markdown)
        async with self.sessions() as db, db.begin():
            page = await self._get_page(db, url)
            is_new = page is None
            if page is None:
                page = Page(site_id=self.site.id, canonical_url=url, url=fetched_url, version=0,
                            meta={})
                db.add(page)
            changed = is_new or page.content_hash != h or page.status != "active"
            ts = now()
            page.url = fetched_url
            page.title = (ex.title or "")[:500] or None
            page.http_status = 200
            # A rendered page's static ETag describes the shell, not the content: don't keep it.
            page.etag = None if rendered else headers.get("etag")
            page.last_modified = None if rendered else headers.get("last-modified")
            page.status = "active"
            page.fetched_at = page.verified_at = ts
            page.meta = {**(page.meta or {}), "links": links[: self.s.max_links_stored_per_page],
                         "published": ex.published, "rendered": rendered}
            if changed:
                page.version = (page.version or 0) + 1
                page.content_hash = h
                page.changed_at = ts
                await db.flush()  # page.id for new pages
                await db.execute(delete(Chunk).where(Chunk.page_id == page.id))
                if len(ex.markdown) >= MIN_TEXT_CHARS:
                    pieces = chunk_markdown(
                        ex.markdown, self.s.chunk_target_chars, self.s.chunk_overlap_chars
                    )
                    db.add_all(
                        Chunk(page_id=page.id, page_version=page.version, chunk_index=i,
                              heading_path=p.heading_path, text=p.text, tokens=p.tokens)
                        for i, p in enumerate(pieces)
                    )
            await db.flush()
            status = "new" if is_new else ("changed" if changed else "unchanged")
            return status, page.id
