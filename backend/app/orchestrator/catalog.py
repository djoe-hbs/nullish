"""A short "catalog card" per configured website: what it is, its home-page introduction and a
sample of its page titles. Used to answer questions about the sites as a whole, and to explain
what the sites DO cover when a search finds nothing."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.urls import canonicalize
from app.db.models import Chunk, Page, Site
from app.retrieval.search import Candidate

INTRO_CHARS = 700
MAX_TITLES = 12


async def build_catalog(db: AsyncSession, sites: list[Site]) -> list[Candidate]:
    cards: list[Candidate] = []
    for s in sites:
        root = canonicalize(s.root_url) or s.root_url
        n_pages, last_fetch = (await db.execute(
            select(func.count(), func.max(Page.fetched_at))
            .where(Page.site_id == s.id, Page.status == "active")
        )).one()
        titles: list[str] = []
        for t in await db.scalars(
            select(Page.title).where(Page.site_id == s.id, Page.status == "active",
                                     Page.title.is_not(None)).order_by(Page.id).limit(60)
        ):
            t = t.strip()
            if t and t not in titles:
                titles.append(t)
            if len(titles) >= MAX_TITLES:
                break
        intro = await db.scalar(
            select(Chunk.text).join(Page, Page.id == Chunk.page_id)
            .where(Page.site_id == s.id, Page.status == "active",
                   Page.canonical_url.in_([root, root.rstrip("/")]),
                   Chunk.page_version == Page.version)
            .order_by(Chunk.chunk_index).limit(1)
        ) or ""
        parts = [
            s.description or "",
            f"Home page: {intro[:INTRO_CHARS]}" if intro else "",
            f"Some pages: {'; '.join(titles)}" if titles else "",
            f"Indexed pages: {n_pages}",
        ]
        cards.append(Candidate(
            chunk_id=-s.id, page_id=-s.id, site_id=s.id, site_name=s.name, url=s.root_url,
            title=s.name, heading_path="", text="\n".join(p for p in parts if p),
            fetched_at=last_fetch,
        ))
    return cards
