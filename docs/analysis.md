# Smish: design review for the local-app + Vast.ai GPU deployment

**Deployment.** This Windows PC runs the frontend, API, worker, Postgres and Redis, all in Docker Desktop. A rented Vast.ai GPU, switched on and off to save cost, runs **all models** as three vLLM servers: chat/router, embeddings and reranker. This PC reaches them through one SSH tunnel.

**Why embeddings aren't local (measured 2026-11-24).** The PC's CPU is an AMD Ryzen 5 PRO 3500U, a 4-core laptop chip. On TEI's CPU backend, Qwen3-Embedding-0.6B:
- warmed up in about 10 min and peaked at about 5.8 GB RAM;
- took **about 31 s per 600-token chunk** (16 chunks took 9 min) and 1.1 s for a short query.

Indexing one 1,000-page site would take more than a day. On the GPU the same work takes milliseconds.

Moving embeddings to the GPU loses nothing at question time, because chat needs the GPU anyway. The only effect is that **embedding of newly crawled chunks waits for the GPU**. Crawling, extraction and chunking keep running locally.

## Design changes carried into the build

1. **The reranker is not an OpenAI API.** vLLM serves `/v1/rerank` and TEI serves `/rerank`. `app/llm/rerank.py` has adapters for both. The primary is vLLM on the GPU, with an optional CPU TEI fallback (compose profile `cpu-models`).
2. **The GPU being offline is a normal state.**
   - The worker pings all model endpoints every 30 s.
   - `/api/v1/health` reports `gpu_available` separately from the local core (DB + Redis).
   - The UI shows GPU services as grey "offline", not red.
3. **Embedding backlog (Phase 1).** `chunks.embedding` is nullable. Chunks are written right after extraction, and an embed job drains `embedding IS NULL` whenever the embedding endpoint is healthy.
   - Changed pages replace their chunks atomically. Keyword search sees the current version immediately. Vector search picks up its chunks as embeddings arrive.
4. **The router shares the chat model** by default, so only one large model sits in VRAM.
5. **The embedding dimension is pinned** (`EMBED_DIM=1024`). `EmbedClient` refuses vectors of the wrong size. A model with a different dimension must go through the re-embed job. Embeddings never fall back to another endpoint, which the DB enforces with a check constraint.
6. **Filtered vector search per site:** pgvector 0.8.7 (installed) with `hnsw.iterative_scan = relaxed_order`, so site-scoped HNSW queries return enough rows.
7. **Vast.ai networking.** The IP and port change on every rental, and open ports are public. So:
   - vLLM binds to `127.0.0.1` on the instance and uses `--api-key`.
   - `gpu/tunnel.ps1` forwards localhost 8001/8002/8003 (chat/embed/rerank) over one SSH connection.
   - Containers reach the tunnel via `host.docker.internal` (verified working with a 127.0.0.1-only bind), so `.env` never changes between rentals.
8. **GPU sizing: test first, then scale up** (`gpu/start_vllm.sh test|prod`).
   - **test:** `Qwen3-4B-Instruct-2507-FP8` on a cheap 16–24 GB card, for building and checking every flow.
   - **prod:** `Qwen3-30B-A3B-Instruct-2507-FP8` on a 48 GB card.
   - The embedding model and reranker are the same in both, so the index carries over.
   - Models are served under fixed aliases (`nullish-chat/-embed/-rerank`), so switching needs no local change.
   - Health reads each alias's real model from `/v1/models`, and flags an embedding model that differs from `EMBED_MODEL_ID`.
   - Keep `HF_HOME` on the instance disk and *stop* (don't destroy) the instance.
   - Check the exact model IDs and licenses on Hugging Face before the first pull.
9. **Windows host notes.**
   - Everything runs in Docker Desktop (WSL2). Playwright runs in the Linux worker image, which needs `libatomic1`.
   - Host ports moved to 55432 (Postgres) and 56379 (Redis), because 5432, 5433 and 6379 were already taken on this PC.
   - The frontend is on host port 3000. After a restart, Hyper-V reserved 5158–5257, which covers Vite's default 5173. Check with `netsh int ipv4 show excludedportrange protocol=tcp`.
   - Docker has about 10.6 GB RAM.
10. **Open questions in §18 of the design**, with the defaults used until decided:
    - single admin;
    - no `org_id` yet (`TimestampMixin` is the hook for adding it);
    - English-first prompts;
    - unverified pages are labeled, not excluded.
