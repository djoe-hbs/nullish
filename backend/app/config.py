from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://nullish:nullish@localhost:5432/nullish"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "change-me"
    encryption_key: str = "change-me"
    frontend_origin: str = "http://localhost:3000"

    # --- Login ---
    # First admin account, created on first start only when no users exist yet.
    admin_email: str = ""
    admin_password: str = ""
    session_days: int = 7
    cookie_secure: bool = False  # True in production (HTTPS): cookie never sent over plain HTTP

    crawler_user_agent: str = "SmishBot/1.0"
    max_live_fetches_per_question: int = 8
    live_fetch_timeout_sec: float = 4.0

    # All models run on the rented GPU (vLLM), reached via the SSH tunnel on the host.
    gpu_api_key: str = "change-me"  # vLLM --api-key, shared by the three servers

    # Model names are the vLLM --served-model-name aliases (see gpu/start_vllm.sh), so switching
    # the GPU between the "test" (small) and "prod" (large) chat model needs no change here.
    chat_base_url: str = "http://host.docker.internal:8001/v1"
    chat_model: str = "nullish-chat"
    router_base_url: str = ""  # empty -> share chat endpoint
    router_model: str = ""

    embed_model: str = "nullish-embed"
    # The real model the index was built with. Health flags the embed server if it serves
    # anything else (vectors from another model would silently corrupt retrieval).
    embed_model_id: str = "Qwen/Qwen3-Embedding-0.6B"
    embed_url: str = "http://host.docker.internal:8002"
    embed_dim: int = 1024
    rerank_model: str = "nullish-rerank"
    rerank_url: str = "http://host.docker.internal:8003"
    rerank_kind: str = "vllm"  # "vllm" (/v1/rerank) | "tei" (/rerank)
    rerank_max_candidates: int = 30
    # Optional CPU TEI reranker (compose profile "cpu-models") used when the GPU one fails.
    rerank_fallback_url: str = ""

    health_timeout_sec: float = 3.0

    # --- Crawler ---
    # Only for local test sites: lets the crawler fetch private/loopback addresses (SSRF guard off).
    allow_private_urls: bool = False
    crawl_concurrency: int = 2  # parallel fetches per site
    crawl_fetch_timeout_sec: float = 15.0
    max_page_bytes: int = 5_000_000
    max_links_stored_per_page: int = 300

    # --- Indexing / retrieval ---
    chunk_target_chars: int = 2000  # ~500 tokens
    chunk_overlap_chars: int = 300  # ~15 %
    embed_batch_size: int = 32
    retrieval_candidates: int = 40  # per search arm (vector, keyword) before fusion
    answer_sources: int = 6  # chunks given to the chat model
    max_chunks_per_page: int = 3  # diversity: one long page can't fill every source slot
    # bge-reranker scores are compressed (a clearly relevant passage scored 0.03 in testing), so
    # only near-zero noise is dropped. Tune with real questions.
    min_rerank_score: float = 0.001

    # --- Live verification at question time ("always live") ---
    live_verify_pages: int = 6  # top pages re-checked against the live site per question
    live_discovery_per_site: int = 3  # not-yet-indexed pages fetched live per site (sitemap/search)
    live_render_timeout_sec: float = 12.0  # JavaScript-rendered pages (headless browser)
    live_total_budget_sec: float = 15.0  # whole verification step; slower pages -> "unverified"
    # Parallel live fetches per site. = live_verify_pages, so all top pages are checked in one
    # wave (JavaScript pages take ~3-4 s each to render; two waves doubled the wait).
    live_per_site_concurrency: int = 6
    # A page confirmed live this recently isn't fetched again (follow-up questions stay fast).
    # Its label shows the real verification time. 0 = fetch on every single question.
    live_reverify_after_sec: int = 60
    sitemap_cache_sec: int = 300
    robots_cache_sec: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
