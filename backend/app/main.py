from contextlib import asynccontextmanager

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.api.auth import ensure_admin
from app.config import get_settings
from app.crawler.browser import Browser
from app.crawler.fetcher import Fetcher
from app.db.session import SessionLocal
from app.freshness.live import LiveVerifier
from app.llm.embed import get_embedder


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    async with SessionLocal() as db:
        if await ensure_admin(db, s):
            structlog.get_logger().info("admin_created", email=s.admin_email)
    # Job queue shared with the worker (crawl jobs are enqueued here, run there).
    app.state.arq = await create_pool(RedisSettings.from_dsn(s.redis_url))
    # Live verification at question time: one shared HTTP client + one warm headless browser.
    app.state.live = LiveVerifier(
        s,
        SessionLocal,
        Fetcher(s.crawler_user_agent, timeout=s.live_fetch_timeout_sec,
                max_bytes=s.max_page_bytes, allow_private=s.allow_private_urls),
        Browser(s.crawler_user_agent, allow_private=s.allow_private_urls),
        get_embedder(),
    )
    try:
        yield
    finally:
        await app.state.live.close()
        await app.state.arq.aclose()


app = FastAPI(title="Smish", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().frontend_origin.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """CSRF defence in depth (cookies are also SameSite=Lax): a state-changing request that a
    browser sends from another website carries that site's Origin, and is refused."""
    origin = request.headers.get("origin")
    if request.method in UNSAFE and origin and origin.rstrip("/") not in allowed_origins():
        return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
    return await call_next(request)


def allowed_origins() -> set[str]:
    s = get_settings()
    return {o.strip().rstrip("/") for o in s.frontend_origin.split(",") if o.strip()}


app.include_router(api_router)
