"""Embedding client: OpenAI-compatible /v1/embeddings (vLLM on the GPU; TEI also works).

Embeddings are pinned to ONE endpoint (no fallback): vectors from a different model would corrupt
the index. A model with a different dimension must go through the re-embed job.
"""

from functools import lru_cache

import httpx

from app.config import Settings, get_settings

# Qwen3-Embedding is instruction-aware on the QUERY side only; documents are embedded as-is.
QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"


class EmbeddingDimensionError(RuntimeError):
    pass


class EmbedClient:
    def __init__(self, s: Settings, timeout: float = 60.0):
        self.url = f"{s.embed_url.rstrip('/')}/v1/embeddings"
        self.model = s.embed_model
        self.dim = s.embed_dim
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {s.gpu_api_key}"}
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        # One long-lived client: keeps the connection over the SSH tunnel open between calls.
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout, headers=self.headers)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client().post(self.url, json={"model": self.model, "input": texts})
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        vectors = [d["embedding"] for d in data]
        for v in vectors:
            if len(v) != self.dim:
                raise EmbeddingDimensionError(
                    f"model returned {len(v)} dims, index expects {self.dim}; run a re-embed job"
                )
        return vectors

    async def embed_query(self, query: str) -> list[float]:
        return (await self.embed([f"Instruct: {QUERY_INSTRUCTION}\nQuery: {query}"]))[0]


def document_text(title: str | None, heading_path: str, text: str) -> str:
    """What gets embedded for a chunk: page title and section heading add useful context."""
    head = " — ".join(p for p in (title, heading_path) if p)
    return f"{head}\n\n{text}" if head else text


@lru_cache
def get_embedder() -> EmbedClient:
    return EmbedClient(get_settings())
