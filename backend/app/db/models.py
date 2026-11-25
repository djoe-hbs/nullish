from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)


class LLMEndpoint(TimestampMixin, Base):
    __tablename__ = "llm_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str] = mapped_column(Text)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # chat | embedding | rerank
    # rerank adapter ("tei" | "vllm"); embedding dim; generation params
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_latency_ms: Mapped[float | None] = mapped_column(Float)


class ModelRole(Base):
    __tablename__ = "model_roles"

    # chat | router | embedding | reranker
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("llm_endpoints.id", ondelete="RESTRICT"))
    # Optional fallback (e.g. reranker on GPU while up, CPU otherwise). Not allowed for embedding.
    fallback_endpoint_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_endpoints.id", ondelete="SET NULL")
    )


# --------------------------------------------------------------------------------------------
# Sites, pages, chunks (Phase 1)
# --------------------------------------------------------------------------------------------

EMBED_DIM = 1024  # must match Settings.embed_dim and the migration's vector(1024)


class Site(TimestampMixin, Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    root_url: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    render_mode: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto")
    include_patterns: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default="{}"
    )
    exclude_patterns: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default="{}"
    )
    max_depth: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    max_pages: Mapped[int] = mapped_column(Integer, default=200, server_default="200")
    freshness_max_age_sec: Mapped[int] = mapped_column(
        Integer, default=3600, server_default="3600"
    )
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    crawl_delay_ms: Mapped[int] = mapped_column(Integer, default=500, server_default="500")
    # Optional on-site search URL with a {query} placeholder, used for live discovery.
    site_search_url: Mapped[str | None] = mapped_column(Text)


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("site_id", "canonical_url", name="uq_pages_site_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | gone | error
    http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last time the content was confirmed against the live site (304 / same text / new text).
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # outlinks (so a 304 Not Modified page can still be followed), canonical, etc.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    page_version: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str] = mapped_column(Text, default="", server_default="")
    text: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer)
    # NULL until the embed worker processes it (the GPU may be off when a page is crawled).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', heading_path || ' ' || text)", persisted=True),
    )


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20), default="full")
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued|running|done|failed
    pages_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_changed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_unchanged: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_removed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    errors: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------------------------
# Accounts and sessions
# --------------------------------------------------------------------------------------------


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)  # Argon2id
    role: Mapped[str] = mapped_column(String(20), default="admin", server_default="admin")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(TimestampMixin, Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the cookie value
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
