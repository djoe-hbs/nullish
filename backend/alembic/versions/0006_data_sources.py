"""database connections: data sources, schema items, pending changes, query log

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def source_fk() -> sa.Column:
    return sa.Column("source_id", sa.Integer(),
                     sa.ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False,
                     index=True)


def user_fk(name: str = "user_id") -> sa.Column:
    return sa.Column(name, sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"))


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_encrypted", sa.Text()),
        sa.Column("ssl_mode", sa.String(10), nullable=False, server_default="prefer"),
        sa.Column("edit_policy", sa.String(10), nullable=False, server_default="read_only"),
        sa.Column("row_limit", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("timeout_sec", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("description", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        ts("last_test_at"),
        sa.Column("last_test_ok", sa.Boolean()),
        sa.Column("last_error", sa.Text()),
        sa.Column("server_version", sa.Text()),
        ts("schema_refreshed_at"),
        sa.Column("schema_error", sa.Text()),
        ts("created_at", nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('postgres','mysql')", name="ck_ds_kind"),
        sa.CheckConstraint("edit_policy IN ('read_only','ask','auto')", name="ck_ds_policy"),
        sa.CheckConstraint("ssl_mode IN ('disable','prefer','require','verify')",
                           name="ck_ds_ssl"),
    )
    op.create_table(
        "ds_schema_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        source_fk(),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("column_name", sa.Text()),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_type", sa.Text()),
        sa.Column("nullable", sa.Boolean()),
        sa.Column("is_pk", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fk_ref", sa.Text()),
        sa.Column("row_estimate", sa.BigInteger()),
        sa.Column("sample_values", JSONB(), nullable=False, server_default="[]"),
        sa.Column("comment", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("embedding", Vector(1024)),
    )
    op.create_table(
        "ds_pending_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        source_fk(),
        user_fk(),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("sql_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("preview", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("result", JSONB()),
        ts("created_at", nullable=False, server_default=sa.func.now()),
        ts("expires_at", nullable=False),
        ts("decided_at"),
        user_fk("decided_by"),
    )
    op.create_table(
        "ds_query_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        source_fk(),
        user_fk(),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("rows", sa.Integer()),
        sa.Column("ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("pending_id", sa.Integer(),
                  sa.ForeignKey("ds_pending_changes.id", ondelete="SET NULL")),
        ts("created_at", nullable=False, server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("ds_query_log")
    op.drop_table("ds_pending_changes")
    op.drop_table("ds_schema_items")
    op.drop_table("data_sources")
