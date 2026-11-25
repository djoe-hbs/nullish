"""Drains the embedding queue: chunks whose `embedding IS NULL`.

Runs from the worker (cron + right after each crawl). If the GPU is off the embed call fails,
the transaction rolls back, and the chunks simply wait for the next run.
"""

import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Chunk, Page
from app.llm.embed import EmbedClient, document_text


async def pending_count(db: AsyncSession) -> int:
    from sqlalchemy import func

    return await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.embedding.is_(None)))


async def embed_page(
    session_factory: async_sessionmaker[AsyncSession], client: EmbedClient, page_id: int
) -> int:
    """Embed one page's waiting chunks right away (live verification found new text)."""
    async with session_factory() as db, db.begin():
        rows = (
            await db.execute(
                select(Chunk.id, Chunk.heading_path, Chunk.text, Page.title)
                .join(Page, Page.id == Chunk.page_id)
                .where(Chunk.page_id == page_id, Chunk.embedding.is_(None))
                .order_by(Chunk.id)
                .with_for_update(of=Chunk, skip_locked=True)
            )
        ).all()
        if not rows:
            return 0
        texts = [document_text(r.title, r.heading_path, r.text) for r in rows]
        vectors = await client.embed(texts)
        await db.execute(
            update(Chunk),
            [{"id": r.id, "embedding": v} for r, v in zip(rows, vectors, strict=True)],
        )
        return len(rows)


async def embed_pending(
    session_factory: async_sessionmaker[AsyncSession],
    client: EmbedClient,
    *,
    batch_size: int = 32,
    max_seconds: float = 50.0,
) -> int:
    """Embed up to `max_seconds` worth of batches. Returns the number of chunks embedded."""
    deadline = time.monotonic() + max_seconds
    total = 0
    while time.monotonic() < deadline:
        async with session_factory() as db, db.begin():
            rows = (
                await db.execute(
                    select(Chunk.id, Chunk.heading_path, Chunk.text, Page.title)
                    .join(Page, Page.id == Chunk.page_id)
                    .where(Chunk.embedding.is_(None))
                    .order_by(Chunk.id)
                    .limit(batch_size)
                    # Row locks: a re-crawl deleting these chunks waits; parallel runs skip them.
                    .with_for_update(of=Chunk, skip_locked=True)
                )
            ).all()
            if not rows:
                break
            vectors = await client.embed(
                [document_text(r.title, r.heading_path, r.text) for r in rows]
            )
            await db.execute(
                update(Chunk),
                [{"id": r.id, "embedding": v} for r, v in zip(rows, vectors, strict=True)],
            )
        total += len(rows)
    return total
