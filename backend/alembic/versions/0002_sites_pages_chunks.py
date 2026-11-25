"""sites, pages, chunks (pgvector + full-text), crawl_jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25
"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("root_url", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("render_mode", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("include_patterns", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default="{}"),
        sa.Column("exclude_patterns", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default="{}"),
        sa.Column("max_depth", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_pages", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("freshness_max_age_sec", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("respect_robots", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("crawl_delay_ms", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("render_mode IN ('auto','static','browser')",
                           name="ck_sites_render_mode"),
    )

    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("http_status", sa.Integer()),
        sa.Column("etag", sa.Text()),
        sa.Column("last_modified", sa.Text()),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("changed_at", sa.DateTime(timezone=True)),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("site_id", "canonical_url", name="uq_pages_site_url"),
        sa.CheckConstraint("status IN ('active','gone','error')", name="ck_pages_status"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("page_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024)),
        sa.Column(
            "tsv", postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', heading_path || ' ' || text)", persisted=True),
        ),
    )
    # Vector search (cosine). Rows with NULL embedding are simply not in the index.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
    # The embed worker's queue: chunks still waiting for a vector.
    op.execute("CREATE INDEX ix_chunks_unembedded ON chunks (id) WHERE embedding IS NULL")

    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("type", sa.String(20), nullable=False, server_default="full"),
        sa.Column("status", sa.String(10), nullable=False, server_default="queued"),
        sa.Column("pages_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued','running','done','failed')",
                           name="ck_crawl_jobs_status"),
    )


def downgrade() -> None:
    op.drop_table("crawl_jobs")
    op.drop_table("chunks")
    op.drop_table("pages")
    op.drop_table("sites")
