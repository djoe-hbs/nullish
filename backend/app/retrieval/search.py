"""Hybrid retrieval: pgvector cosine + Postgres full-text, fused with Reciprocal Rank Fusion,
then reranked by the cross-encoder.

Only the CURRENT version of ACTIVE pages on ENABLED sites is searchable (stale or removed pages
can never be retrieved). Chunks still waiting for an embedding are found by keyword search.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.embed import EmbedClient, document_text
from app.llm.rerank import RerankClient

log = structlog.get_logger()

RRF_K = 60

_FILTERS = """
    s.enabled AND p.status = 'active' AND c.page_version = p.version
    AND (CAST(:site_ids AS int[]) IS NULL OR p.site_id = ANY(CAST(:site_ids AS int[])))
    AND (CAST(:owner_id AS int) IS NULL OR s.user_id = CAST(:owner_id AS int))
"""

VECTOR_SQL = text(f"""
    SELECT c.id FROM chunks c
    JOIN pages p ON p.id = c.page_id JOIN sites s ON s.id = p.site_id
    WHERE c.embedding IS NOT NULL AND {_FILTERS}
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :k
""")

# OR-ed terms (plainto_tsquery ANDs every word, which misses most natural-language questions);
# ts_rank_cd still ranks chunks matching more of the terms higher.
KEYWORD_SQL = text(f"""
    WITH q AS (
        SELECT to_tsquery('english', NULLIF(
            replace(plainto_tsquery('english', :q)::text, ' & ', ' | '), '')) AS query
    )
    SELECT c.id FROM chunks c
    JOIN pages p ON p.id = c.page_id JOIN sites s ON s.id = p.site_id, q
    WHERE q.query IS NOT NULL AND c.tsv @@ q.query AND {_FILTERS}
    ORDER BY ts_rank_cd(c.tsv, q.query) DESC
    LIMIT :k
""")

_COLUMNS = """
    c.id AS chunk_id, c.page_id, p.site_id, s.name AS site_name, p.url, p.title,
    c.heading_path, c.text, p.fetched_at, p.verified_at
"""

DETAILS_SQL = text(f"""
    SELECT {_COLUMNS}
    FROM chunks c JOIN pages p ON p.id = c.page_id JOIN sites s ON s.id = p.site_id
    WHERE c.id = ANY(CAST(:ids AS bigint[]))
""")

# Current chunks of given pages (after a live check rewrote them).
PAGE_CHUNKS_SQL = text(f"""
    SELECT {_COLUMNS}
    FROM chunks c JOIN pages p ON p.id = c.page_id JOIN sites s ON s.id = p.site_id
    WHERE c.page_id = ANY(CAST(:pids AS int[])) AND p.status = 'active'
      AND c.page_version = p.version
    ORDER BY c.page_id, c.chunk_index
""")


@dataclass
class Candidate:
    chunk_id: int
    page_id: int
    site_id: int
    site_name: str
    url: str
    title: str | None
    heading_path: str
    text: str
    fetched_at: datetime | None
    verified_at: datetime | None = None
    score: float = 0.0
    # indexed (not re-checked) | verified (unchanged on the live site) | updated | new
    # | unverified (live check failed: content may be stale)
    live: str = "indexed"


@dataclass
class RetrievalResult:
    sources: list[Candidate]
    stats: dict = field(default_factory=dict)
    ranked: list[Candidate] = field(default_factory=list)  # all candidates, best first


def rrf(*rankings: list[int], k: int = RRF_K) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda c: scores[c], reverse=True)


def diversify(cands: list[Candidate], per_page: int, limit: int) -> list[Candidate]:
    out: list[Candidate] = []
    count: dict[int, int] = {}
    for c in cands:
        if count.get(c.page_id, 0) >= per_page:
            continue
        count[c.page_id] = count.get(c.page_id, 0) + 1
        out.append(c)
        if len(out) >= limit:
            break
    return out


async def load_page_chunks(db: AsyncSession, page_ids: list[int]) -> list[Candidate]:
    if not page_ids:
        return []
    rows = (await db.execute(PAGE_CHUNKS_SQL, {"pids": page_ids})).mappings().all()
    return [Candidate(**r) for r in rows]


async def rerank_into(reranker: RerankClient, query: str, cands: list[Candidate]) -> None:
    """Score candidates in place (batches, so no candidate is cut off by the batch limit)."""
    size = reranker.max_candidates
    batches = [cands[i:i + size] for i in range(0, len(cands), size)]

    async def run(batch: list[Candidate]) -> None:
        results = await reranker.rerank(
            query, [document_text(c.title, c.heading_path, c.text) for c in batch]
        )
        for r in results:
            batch[r.index].score = r.score

    await asyncio.gather(*(run(b) for b in batches))


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def retrieve(
    db: AsyncSession,
    query: str,
    *,
    settings: Settings,
    embedder: EmbedClient | None,
    reranker: RerankClient | None,
    site_ids: list[int] | None = None,
    owner_id: int | None = None,
) -> RetrievalResult:
    t0 = time.perf_counter()
    stats: dict = {}
    k = settings.retrieval_candidates
    params = {"site_ids": site_ids or None, "owner_id": owner_id, "k": k}

    vector_ids: list[int] = []
    if embedder is not None:
        try:
            qvec = await embedder.embed_query(query)
            # Filtered HNSW search: keep scanning until enough rows pass the site/page filters.
            async with db.begin_nested():
                await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
                await db.execute(text("SET LOCAL hnsw.ef_search = 100"))
                rows = await db.scalars(VECTOR_SQL, {**params, "qvec": vector_literal(qvec)})
                vector_ids = list(rows)
        except Exception as exc:  # noqa: BLE001 - degrade to keyword-only
            stats["vector_error"] = f"{type(exc).__name__}"
            log.warning("vector_search_failed", error=str(exc))
    keyword_ids = list((await db.execute(KEYWORD_SQL, {**params, "q": query})).scalars())
    stats.update(vector_hits=len(vector_ids), keyword_hits=len(keyword_ids))

    fused = rrf(vector_ids, keyword_ids)[: settings.rerank_max_candidates]
    if not fused:
        stats["retrieval_ms"] = round((time.perf_counter() - t0) * 1000)
        return RetrievalResult([], stats)

    rows = (await db.execute(DETAILS_SQL, {"ids": fused})).mappings().all()
    by_id = {r["chunk_id"]: Candidate(**r) for r in rows}
    cands = [by_id[c] for c in fused if c in by_id]
    # Positional scores, so later steps can compare candidates even without a reranker.
    for i, c in enumerate(cands):
        c.score = 1.0 / (RRF_K + i + 1)

    if reranker is not None:
        try:
            await rerank_into(reranker, query, cands)
            cands.sort(key=lambda c: c.score, reverse=True)
            stats["reranked"] = True
            stats["top_score"] = round(cands[0].score, 4) if cands else None
            # Drop near-zero noise (it tempts a small model to "use" irrelevant text), but always
            # keep a couple so the model itself can say "not found".
            relevant = [c for c in cands if c.score >= settings.min_rerank_score]
            cands = relevant if len(relevant) >= 2 else cands[:2]
        except Exception as exc:  # noqa: BLE001 - keep fused order
            stats["rerank_error"] = f"{type(exc).__name__}"
            log.warning("rerank_failed", error=str(exc))

    sources = diversify(cands, settings.max_chunks_per_page, settings.answer_sources)
    stats["retrieval_ms"] = round((time.perf_counter() - t0) * 1000)
    return RetrievalResult(sources, stats, ranked=cands)
