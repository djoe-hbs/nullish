"""End-to-end over a real Postgres (database `nullish_test`) and a fake website (MockTransport).

Covers the core freshness guarantees of the index:
  - robots.txt disallow + scope rules respected
  - changed text -> new page version, old chunks gone (stale text can't be retrieved)
  - 304 Not Modified -> cheap, content kept
  - 404 -> page marked gone, never retrievable
And then keyword retrieval + a grounded answer with citation checking.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from openai import AsyncOpenAI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.crawler.crawler import SiteCrawler
from app.crawler.fetcher import Fetcher
from app.db import models  # noqa: F401 - register tables
from app.db.base import Base
from app.db.models import Chunk, CrawlJob, Page, Site
from app.freshness.live import LiveVerifier
from app.llm.chat import ChatTarget
from app.orchestrator.answer import answer_stream
from app.retrieval.search import retrieve

ROOT = "http://site.test/"


@pytest_asyncio.fixture
async def sessions():
    base_url = get_settings().database_url
    admin = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            q = text("SELECT 1 FROM pg_database WHERE datname = 'nullish_test'")
            if not await conn.scalar(q):
                await conn.execute(text("CREATE DATABASE nullish_test"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {exc}")
    finally:
        await admin.dispose()
    url = base_url.rsplit("/", 1)[0] + "/nullish_test"
    schema = "test_" + uuid4().hex
    setup = create_async_engine(url)
    async with setup.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(url, connect_args={
        "server_settings": {"search_path": f"{schema},public"}})
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    async with setup.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    await setup.dispose()


def page(title: str, body: str, links: str = "", head: str = "") -> str:
    return (f"<html><head><title>{title}</title>{head}</head><body><nav>{links}</nav>"
            f"<main><h1>{title}</h1><p>{body}</p></main></body></html>")


class FakeSite:
    def __init__(self):
        self.requests: list[str] = []
        self.gone: set[str] = set()
        self.price = "£10.00"
        self.etag_a = '"a-v1"'
        self.gift_cards = False  # a page that exists on the site but was never crawled
        self.down: set[str] = set()  # paths that stop responding

    def html(self, path: str) -> str | None:
        nav = '<a href="/a">Returns</a> <a href="/b?utm_source=x">Shoes</a> ' \
              '<a href="/private/x">Secret</a> <a href="http://other.test/">Other</a> ' \
              '<a href="/logo.png">Logo</a>'
        return {
            "/": page("Home", "Welcome to the test store. We sell shoes and accessories. " * 3,
                      nav),
            "/a": page("Return policy", "You can return any item within 30 days of delivery "
                       "for a full refund, as long as it is unused. " * 3, nav),
            # Misconfigured canonical pointing at the home page (common on SPAs): must be ignored.
            "/b": page("Running shoes", f"The Swift runner shoe costs {self.price} and comes in "
                       "three colours: red, blue and black. " * 3, nav,
                       head=f'<link rel="canonical" href="{ROOT}">'),
            "/private/x": page("Secret", "This must never be crawled. " * 5),
            "/gift-cards": page("Gift cards", "Swift gift cards are sold in values of £25, £50 "
                                "and £100 and never expire. " * 3) if self.gift_cards else None,
        }.get(path)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n"
                                  f"Sitemap: {ROOT}sitemap.xml\n")
        if path in self.down:
            raise httpx.ConnectError("connection refused")
        if path == "/sitemap.xml":
            extra = f"<url><loc>{ROOT}gift-cards</loc></url>" if self.gift_cards else ""
            return httpx.Response(200, text=(
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{ROOT}a</loc></url><url><loc>{ROOT}b</loc></url>{extra}</urlset>"))
        if path in self.gone:
            return httpx.Response(404, text="not found")
        body = self.html(path)
        if body is None:
            return httpx.Response(404)
        headers = {"content-type": "text/html; charset=utf-8"}
        if path == "/a":
            headers["etag"] = self.etag_a
            if request.headers.get("if-none-match") == self.etag_a:
                return httpx.Response(304, headers=headers)
        return httpx.Response(200, text=body, headers=headers)


async def run_crawl(sessions, site_obj: Site, fake: FakeSite):
    settings = Settings(_env_file=None, allow_private_urls=True, crawl_concurrency=2)
    async with sessions() as db, db.begin():
        job = CrawlJob(site_id=site_obj.id, status="running", errors=[])
        db.add(job)
    fetcher = Fetcher("test", allow_private=True, transport=httpx.MockTransport(fake.handler))
    try:
        crawler = SiteCrawler(site_obj, job.id, settings, sessions, fetcher, browser=None)
        stats = await crawler.run()
        await crawler.flush_job("done")
    finally:
        await fetcher.close()
    return stats


async def chunks_text(sessions, path: str) -> str:
    async with sessions() as db:
        rows = await db.scalars(
            select(Chunk.text).join(Page, Page.id == Chunk.page_id)
            .where(Page.canonical_url == ROOT.rstrip("/") + path,
                   Chunk.page_version == Page.version)
        )
        return " ".join(rows)


async def test_crawl_versions_304_and_gone_then_answer(sessions):
    async with sessions() as db, db.begin():
        site = Site(name="Test store", root_url=ROOT, crawl_delay_ms=0, max_pages=50,
                    max_depth=3, render_mode="static", include_patterns=[],
                    exclude_patterns=[])
        db.add(site)
    fake = FakeSite()

    # ---- crawl 1: everything new ----
    s1 = await run_crawl(sessions, site, fake)
    assert s1.changed == 3 and s1.failed == 0, s1.errors
    assert "/private/x" not in fake.requests  # robots.txt
    assert "/logo.png" not in fake.requests  # non-HTML skipped
    async with sessions() as db:
        urls = set(await db.scalars(select(Page.canonical_url)))
    assert urls == {ROOT, ROOT + "a", ROOT + "b"}  # tracking param stripped, off-site ignored
    assert "£10.00" in await chunks_text(sessions, "/b")

    # ---- crawl 2: /b text changed, /a answers 304, / unchanged ----
    fake.price = "£12.50"
    s2 = await run_crawl(sessions, site, fake)
    assert (s2.changed, s2.unchanged) == (1, 2), (s2, s2.errors)
    b_text = await chunks_text(sessions, "/b")
    assert "£12.50" in b_text and "£10.00" not in b_text  # stale text is gone
    async with sessions() as db:
        b = await db.scalar(select(Page).where(Page.canonical_url == ROOT + "b"))
        assert b.version == 2
        old = await db.scalar(select(func.count()).select_from(Chunk)
                              .where(Chunk.page_id == b.id, Chunk.page_version == 1))
        assert old == 0

    # ---- crawl 3: /a deleted on the site ----
    fake.gone.add("/a")
    s3 = await run_crawl(sessions, site, fake)
    assert s3.removed == 1
    async with sessions() as db:
        a = await db.scalar(select(Page).where(Page.canonical_url == ROOT + "a"))
        assert a.status == "gone"
    assert await chunks_text(sessions, "/a") == ""

    # ---- retrieval: keyword arm only (no GPU in tests) ----
    settings = Settings(_env_file=None)
    async with sessions() as db:
        res = await retrieve(db, "How much do the Swift runner shoes cost?", settings=settings,
                             embedder=None, reranker=None)
    assert res.sources and res.sources[0].url == ROOT + "b"
    async with sessions() as db:
        res = await retrieve(db, "return policy refund", settings=settings, embedder=None,
                             reranker=None)
    assert all(c.url != ROOT + "a" for c in res.sources)  # gone page never retrieved

    # ---- grounded answer with a fake chat model ----
    answer = "The Swift runner costs £12.50 [1]. It is cheap [9]."
    body = "".join(
        "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 0,
                               "model": "m", "choices": [{"index": 0, "delta": {"content": t},
                                                          "finish_reason": None}]}) + "\n\n"
        for t in (answer[:20], answer[20:])
    ) + "data: [DONE]\n\n"
    sent = {}

    def chat_handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    chat = ChatTarget(AsyncOpenAI(base_url="http://gpu/v1", api_key="k", max_retries=0,
                                  http_client=httpx.AsyncClient(
                                      transport=httpx.MockTransport(chat_handler))), "nullish-chat")
    events = []
    async for raw in answer_stream(
        [{"role": "user", "content": "How much do the Swift runner shoes cost?"}], None,
        settings=settings, sessions=sessions, chat=chat, embedder=None, reranker=None,
    ):
        name, data = raw.strip().split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    kinds = [e for e, _ in events]
    assert kinds[0] == "status" and "sources" in kinds and kinds[-1] == "done"
    sources = next(d for e, d in events if e == "sources")
    assert sources[0]["url"] == ROOT + "b" and sources[0]["fetched_at"]
    done = events[-1][1]
    # "It is cheap [9]" cites a source that doesn't exist: that sentence is removed, the
    # validly cited one is kept.
    shown = next((d["t"] for e, d in reversed(events) if e == "replace"),
                 "".join(d["t"] for e, d in events if e == "token"))
    assert shown == "The Swift runner costs £12.50 [1]."
    assert done["cited"] == [1] and done["invalid_citations"] == [9] and not done["not_found"]
    prompt = sent["messages"][-1]["content"]
    assert "[1] (site: Test store" in prompt and "£12.50" in prompt and "£10.00" not in prompt


# ---- live verification (the plan's freshness test suite) --------------------------------------


def fake_chat(answer: str, sent: list[dict], verdict: str = "PASS") -> ChatTarget:
    body = "data: " + json.dumps({
        "id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": "stop"}],
    }) + "\n\ndata: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        if not payload.get("stream"):
            return httpx.Response(200, json={"id": "review", "model": "m", "created": 0,
                "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": verdict}}]})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return ChatTarget(AsyncOpenAI(base_url="http://gpu/v1", api_key="k", max_retries=0,
                                  http_client=httpx.AsyncClient(
                                      transport=httpx.MockTransport(handler))), "nullish-chat")


async def ask_live(sessions, live: LiveVerifier, question: str):
    sent: list[dict] = []
    events = []
    async for raw in answer_stream(
        [{"role": "user", "content": question}], None, settings=live.s, sessions=sessions,
        chat=fake_chat("Answer [1].", sent), embedder=None, reranker=None, live=live,
    ):
        name, data = raw.strip().split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    sources = next((d for e, d in events if e == "sources"), [])
    verified = next((d for e, d in events if e == "status" and d["stage"] == "verified"), None)
    answer_calls = [r for r in sent if r.get("stream")]  # skip the router's (non-stream) call
    prompt = answer_calls[0]["messages"][-1]["content"] if answer_calls else ""
    return sources, verified, prompt, events


async def test_live_verification_without_recrawl(sessions):
    async with sessions() as db, db.begin():
        site = Site(name="Test store", root_url=ROOT, crawl_delay_ms=0, max_pages=50,
                    max_depth=3, render_mode="static", include_patterns=[],
                    exclude_patterns=[])
        db.add(site)
    fake = FakeSite()
    await run_crawl(sessions, site, fake)

    # Strict: fetch on every question (no "verified moments ago" re-use window).
    settings = Settings(_env_file=None, allow_private_urls=True, sitemap_cache_sec=0,
                        live_reverify_after_sec=0)
    live = LiveVerifier(
        settings, sessions,
        Fetcher("test", allow_private=True, transport=httpx.MockTransport(fake.handler)),
        browser=None, embedder=None,
    )
    try:
        # 1. Price changed on the site, NO re-crawl: the answer must use the new price.
        fake.price = "£12.50"
        sources, verified, prompt, _ = await ask_live(
            sessions, live, "How much do the Swift runner shoes cost?")
        b = next(s for s in sources if s["url"] == ROOT + "b")
        assert b["live"] == "updated" and b["verified_at"]
        assert "£12.50" in prompt and "£10.00" not in prompt
        assert "verified live:" in prompt
        assert verified["changed"] >= 1
        assert "£12.50" in await chunks_text(sessions, "/b")  # the index was updated too

        # 2. Page deleted on the site: never cited, marked gone.
        fake.gone.add("/a")
        sources, verified, prompt, _ = await ask_live(
            sessions, live, "What is the return policy for a refund?")
        assert all(s["url"] != ROOT + "a" for s in sources)
        assert "30 days" not in prompt
        async with sessions() as db:
            a = await db.scalar(select(Page).where(Page.canonical_url == ROOT + "a"))
            assert a.status == "gone"

        # 3. New page that was never crawled: discovered live via the sitemap and used.
        fake.gift_cards = True
        sources, verified, prompt, _ = await ask_live(
            sessions, live, "Do you sell gift cards and what values are available?")
        g = next(s for s in sources if s["url"] == ROOT + "gift-cards")
        assert g["live"] == "new" and verified["discovered"] >= 1
        assert "£25, £50 and £100" in prompt

        # 4. Site stops responding for a page: kept but clearly labelled as unverified.
        fake.down.add("/b")
        sources, verified, prompt, _ = await ask_live(
            sessions, live, "How much do the Swift runner shoes cost?")
        b = next(s for s in sources if s["url"] == ROOT + "b")
        assert b["live"] == "unverified"
        assert "NOT verified live" in prompt and verified.get("error", 0) >= 1
    finally:
        await live.close()

    # 5. Re-use window: a page confirmed live moments ago is not fetched again.
    fake.down.clear()
    windowed = LiveVerifier(
        Settings(_env_file=None, allow_private_urls=True, live_reverify_after_sec=3600),
        sessions, Fetcher("test", allow_private=True, transport=httpx.MockTransport(fake.handler)),
        browser=None, embedder=None,
    )
    try:
        before = fake.requests.count("/")
        sources, verified, _, _ = await ask_live(
            sessions, windowed, "Welcome to the test store: shoes and accessories")
        home = next(s for s in sources if s["url"] == ROOT)
        assert home["live"] == "verified" and verified.get("recently_verified", 0) >= 1
        assert fake.requests.count("/") == before  # no network fetch for the home page
    finally:
        await windowed.close()


async def test_scheduler_repairs_delivery_and_handles_moves(sessions, monkeypatch):
    from redis.exceptions import ConnectionError

    from app.api.sites import start_crawl
    from app.workers import settings as worker

    monkeypatch.setattr(worker, "SessionLocal", sessions)
    queue = AsyncMock()
    async with sessions() as db:
        site = Site(name="Store", root_url=ROOT)
        db.add(site)
        await db.commit()
        queue.enqueue_job.side_effect = ConnectionError()
        job = await start_crawl(db, queue, site)
    queue.enqueue_job.side_effect = None
    await worker.schedule_crawls({"redis": queue})
    queue.enqueue_job.assert_awaited_with("crawl_site_task", site.id, job.id,
                                         _job_id=f"crawl:{job.id}")
    async with sessions() as db, db.begin():
        row = await db.get(CrawlJob, job.id)
        row.status = "done"
        row.finished_at = datetime.now(UTC)
    queue.reset_mock()
    await worker.schedule_crawls({"redis": queue})
    queue.enqueue_job.assert_not_called()
    async with sessions() as db, db.begin():
        row = await db.get(CrawlJob, job.id)
        row.finished_at -= timedelta(hours=2)
    await worker.schedule_crawls({"redis": queue})
    assert queue.enqueue_job.await_count == 1
    async with sessions() as db, db.begin():
        row = await db.scalar(select(CrawlJob).where(CrawlJob.status == "queued"))
        row.status = "done"
        row.finished_at = datetime.now(UTC) - timedelta(seconds=5)
        db.add(Page(site_id=site.id, url=ROOT+"old", canonical_url=ROOT+"old",
                    status="gone", verified_at=datetime.now(UTC)))
    await worker.schedule_crawls({"redis": queue})
    assert queue.enqueue_job.await_count == 2


async def test_redesign_and_vector_failure(sessions, monkeypatch):
    from app.retrieval import search

    async with sessions() as db, db.begin():
        site = Site(name="Store", root_url=ROOT, render_mode="static", crawl_delay_ms=0)
        db.add(site)
    fake = FakeSite()
    await run_crawl(sessions, site, fake)
    original = fake.handler

    def redesigned(req):
        path = req.url.path
        if path == "/":
            html = page("New store", "Our new shop. " * 10, '<a href="/catalog">Browse</a>')
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/b":
            return httpx.Response(301, headers={"location": "/catalog"})
        if path == "/a":
            return httpx.Response(410)
        if path == "/catalog":
            html = page("Catalog", "We sell blue shoes. " * 10)
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        return original(req)

    fake.handler = redesigned
    await run_crawl(sessions, site, fake)
    assert not await chunks_text(sessions, "/a")
    assert not await chunks_text(sessions, "/b")
    assert "blue shoes" in await chunks_text(sessions, "/catalog")
    monkeypatch.setattr(search, "VECTOR_SQL", text("SELECT 1/0"))
    embed = AsyncMock()
    embed.embed_query.return_value = [0.1] * 1024
    async with sessions() as db:
        result = await retrieve(db, "blue shoes", settings=Settings(_env_file=None),
                                embedder=embed, reranker=None)
        assert result.stats["vector_error"] and result.sources[0].url == ROOT + "catalog"

    # Answers stream and are kept as the model wrote them (no canned replacement). An answer
    # without any valid citation is flagged not_found (the model explained it couldn't find it).
    # Exactly one streamed answer call; no second call grades the draft.
    for draft, grounded in [("I couldn't see shoe colours on these pages.", False),
                            ("We sell blue shoes [1].", True)]:
        sent: list[dict] = []
        events = [raw async for raw in answer_stream(
            [{"role": "user", "content": "Do you sell blue shoes?"}], None,
            settings=Settings(_env_file=None), sessions=sessions,
            chat=fake_chat(draft, sent), embedder=None, reranker=None)]
        kinds = [e.split("\n", 1)[0].removeprefix("event: ") for e in events]
        done = json.loads(events[-1].split("data: ", 1)[1])
        assert kinds.index("token") < kinds.index("done")  # streamed, not held back
        assert "replace" not in kinds
        assert any(draft in e for e in events if e.startswith("event: token"))
        assert done["not_found"] == (not grounded)
        assert sum(1 for r in sent if r.get("stream")) == 1


async def test_expanded_company_name_in_profile(sessions):
    from app.orchestrator.answer import resolve_profile

    async with sessions() as db, db.begin():
        site = Site(name="Hudsmers", root_url=ROOT)
        db.add(site)
        await db.flush()
        page = Page(site_id=site.id, url=ROOT+"about", canonical_url=ROOT+"about",
                    status="active", version=1)
        db.add(page)
        await db.flush()
        db.add(Chunk(page_id=page.id, page_version=1, chunk_index=0, heading_path="Who we are",
                     text="Hudsmer Business Solutions builds accessible software.", tokens=9))
    async with sessions() as db:
        matched, profiles = await resolve_profile(db, "Explain abt hudsmer business solutions",
                                                  [site])
        assert matched is site and [c.url for c in profiles] == [ROOT+"about"]
        assert await resolve_profile(db, "Explain Hudsmers pricing", [site]) == (None, [])
