"""Reranker client. Rerank is not part of the OpenAI API, so there are two adapters:
- vLLM: POST /v1/rerank  {"model", "query", "documents"}
        -> {"results": [{"index", "relevance_score"}]}
- TEI:  POST /rerank     {"query", "texts"}              -> [{"index", "score"}]
Primary is the GPU (vLLM). An optional CPU TEI fallback is tried if the primary fails.
"""

from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import Settings, get_settings


@dataclass
class RerankResult:
    index: int
    score: float


class RerankClient:
    def __init__(self, s: Settings, timeout: float = 10.0):
        self.model = s.rerank_model
        self.primary = (s.rerank_url, s.rerank_kind)
        self.fallback = (s.rerank_fallback_url, "tei") if s.rerank_fallback_url else None
        self.max_candidates = s.rerank_max_candidates
        self.headers = {"Authorization": f"Bearer {s.gpu_api_key}"}
        self.timeout = timeout
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        # Long-lived client: reuses the connection over the SSH tunnel between questions.
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout, headers=self.headers)
        return self._http

    async def _call(self, base: str, kind: str, query: str, docs: list[str]) -> list[RerankResult]:
        base = base.rstrip("/")
        client = self._client()
        if kind == "vllm":
            resp = await client.post(
                f"{base}/v1/rerank",
                json={"model": self.model, "query": query, "documents": docs},
            )
            resp.raise_for_status()
            items = [RerankResult(r["index"], r["relevance_score"]) for r in resp.json()["results"]]
        else:
            resp = await client.post(f"{base}/rerank", json={"query": query, "texts": docs})
            resp.raise_for_status()
            items = [RerankResult(r["index"], r["score"]) for r in resp.json()]
        return sorted(items, key=lambda r: r.score, reverse=True)

    async def rerank(self, query: str, docs: list[str]) -> list[RerankResult]:
        docs = docs[: self.max_candidates]
        if not docs:
            return []
        try:
            return await self._call(*self.primary, query, docs)
        except httpx.HTTPError:
            if not self.fallback:
                raise
            return await self._call(*self.fallback, query, docs)


@lru_cache
def get_reranker() -> RerankClient:
    return RerankClient(get_settings())
