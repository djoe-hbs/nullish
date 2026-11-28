from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    __table_args__ = (
        Index(
            "uq_sites_user_root_url",
            "user_id",
            "root_url",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    root_url: Mapped[str] = mapped_column(Text)
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
    __table_args__ = (
        Index(
            "uq_users_username_lower",
            text("lower(username)"),
            unique=True,
            postgresql_where=text("username IS NOT NULL"),
        ),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    password_hash: Mapped[str] = mapped_column(Text)  # Argon2id
    role: Mapped[str] = mapped_column(String(20), default="admin", server_default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_mode: Mapped[str | None] = mapped_column(String(16))  # None | 'db' | 'local'
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


class AuthToken(TimestampMixin, Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('verify_email','reset_password')",
            name="ck_auth_token_purpose",
        ),
        Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------------------------
# Conversations & messages (stored in Postgres per user)
# --------------------------------------------------------------------------------------------


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("title_source IN ('auto','llm','user')", name="ck_conv_title_source"),
        CheckConstraint("mode IN ('sites','model')", name="ck_conv_mode"),
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="New chat", server_default="New chat")
    title_source: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto")
    mode: Mapped[str] = mapped_column(String(10), default="sites", server_default="sites")
    selected_sources: Mapped[list[int]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    summary: Mapped[str | None] = mapped_column(Text)
    summary_through_message_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role IN ('user','assistant')", name="ck_messages_role"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(String(10))  # sites | model
    error: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list[Any] | None] = mapped_column(JSONB)
    queries: Mapped[list[Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    intent: Mapped[str | None] = mapped_column(String(30))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    not_found: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


# --------------------------------------------------------------------------------------------
# Database connections ("ask your database")
# --------------------------------------------------------------------------------------------

EDIT_POLICIES = ("read_only", "ask", "auto")
DB_KINDS = ("postgres", "mysql")
SSL_MODES = ("disable", "prefer", "require", "verify")


class DataSource(TimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        Index(
            "uq_ds_user_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # postgres | mysql
    host: Mapped[str] = mapped_column(Text)
    port: Mapped[int] = mapped_column(Integer)
    database: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(Text)
    password_encrypted: Mapped[str | None] = mapped_column(Text)  # never returned by the API
    ssl_mode: Mapped[str] = mapped_column(String(10), default="prefer", server_default="prefer")
    # read_only: never edit | ask: every change needs approval | auto: data changes run
    # directly (schema changes still need approval)
    edit_policy: Mapped[str] = mapped_column(
        String(10), default="read_only", server_default="read_only"
    )
    row_limit: Mapped[int] = mapped_column(Integer, default=200, server_default="200")
    timeout_sec: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(Text)
    server_version: Mapped[str | None] = mapped_column(Text)
    schema_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_error: Mapped[str | None] = mapped_column(Text)


class DsSchemaItem(Base):
    """One table (column_name NULL) or one column of a connected database."""

    __tablename__ = "ds_schema_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    schema_name: Mapped[str] = mapped_column(Text)
    table_name: Mapped[str] = mapped_column(Text)
    column_name: Mapped[str | None] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    data_type: Mapped[str | None] = mapped_column(Text)  # column type, or "table" / "view"
    nullable: Mapped[bool | None] = mapped_column(Boolean)
    is_pk: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    fk_ref: Mapped[str | None] = mapped_column(Text)  # "table.column"
    row_estimate: Mapped[int | None] = mapped_column(BigInteger)
    sample_values: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    comment: Mapped[str | None] = mapped_column(Text)  # from the database itself
    note: Mapped[str | None] = mapped_column(Text)  # the user's explanation (glossary)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))


class DsPendingChange(Base):
    """A change waiting for the user's approval."""

    __tablename__ = "ds_pending_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sql: Mapped[str] = mapped_column(Text)
    sql_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10))  # write | ddl
    reason: Mapped[str | None] = mapped_column(Text)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # pending | applied | rejected | expired | failed
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DsQueryLog(Base):
    """Audit log: every statement Smish ran, refused or queued against a connected database."""

    __tablename__ = "ds_query_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sql: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10))  # read | write | ddl | blocked
    # ok | error | refused | pending | applied | rejected
    status: Mapped[str] = mapped_column(String(10))
    rows: Mapped[int | None] = mapped_column(Integer)
    ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    pending_id: Mapped[int | None] = mapped_column(
        ForeignKey("ds_pending_changes.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
