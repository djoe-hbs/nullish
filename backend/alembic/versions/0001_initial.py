"""initial: pgvector extension, settings, llm_endpoints, model_roles

Revision ID: 0001
Revises:
Create Date: 2026-11-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
    )

    op.create_table(
        "llm_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text()),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_ok", sa.Boolean()),
        sa.Column("last_latency_ms", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('chat','embedding','rerank')", name="ck_llm_endpoints_kind"),
    )

    op.create_table(
        "model_roles",
        sa.Column("role", sa.String(20), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(),
                  sa.ForeignKey("llm_endpoints.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fallback_endpoint_id", sa.Integer(),
                  sa.ForeignKey("llm_endpoints.id", ondelete="SET NULL")),
        sa.CheckConstraint("role IN ('chat','router','embedding','reranker')",
                           name="ck_model_roles_role"),
        # Embeddings are pinned to one model: no fallback allowed.
        sa.CheckConstraint("role <> 'embedding' OR fallback_endpoint_id IS NULL",
                           name="ck_model_roles_embedding_no_fallback"),
    )


def downgrade() -> None:
    op.drop_table("model_roles")
    op.drop_table("llm_endpoints")
    op.drop_table("settings")
