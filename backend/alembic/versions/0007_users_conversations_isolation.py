"""users (username, name, is_active), per-user source isolation, conversations and messages

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27
"""
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64)))
    op.add_column("users", sa.Column("name", sa.String(120), nullable=False, server_default=""))
    op.add_column(
        "users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true")
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, email FROM users ORDER BY id")).all()
    used_usernames: set[str] = set()
    for uid, email in rows:
        local = (email or "user").split("@", 1)[0].lower()
        base = re.sub(r"[^a-z0-9._-]+", "_", local).strip("._-") or "user"
        base = base[:50]
        candidate = base
        if candidate in used_usernames:
            candidate = f"{base}_{uid}"
        used_usernames.add(candidate)
        display_name = (email or "Admin").split("@", 1)[0][:120]
        conn.execute(
            sa.text("UPDATE users SET username = :u, name = :n WHERE id = :id"),
            {"u": candidate, "n": display_name, "id": uid},
        )

    op.execute(
        "CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username)) "
        "WHERE username IS NOT NULL"
    )
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")

    # Per-user ownership on sites and data_sources.
    op.add_column(
        "sites",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
    )
    op.create_index("ix_sites_user_id", "sites", ["user_id"])
    op.execute(
        "UPDATE sites SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1) "
        "WHERE user_id IS NULL"
    )
    op.execute("ALTER TABLE sites DROP CONSTRAINT IF EXISTS sites_root_url_key")
    op.execute(
        "CREATE UNIQUE INDEX uq_sites_user_root_url ON sites (user_id, root_url) "
        "WHERE user_id IS NOT NULL"
    )

    op.add_column(
        "data_sources",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
    )
    op.create_index("ix_data_sources_user_id", "data_sources", ["user_id"])
    op.execute(
        "UPDATE data_sources SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1) "
        "WHERE user_id IS NULL"
    )
    op.execute("ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_name_key")
    op.execute(
        "CREATE UNIQUE INDEX uq_ds_user_name ON data_sources (user_id, name) "
        "WHERE user_id IS NOT NULL"
    )

    # Postgres-backed conversations and messages.
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False, server_default="New chat"),
        sa.Column("title_source", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("mode", sa.String(10), nullable=False, server_default="sites"),
        sa.Column("selected_sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("title_source IN ('auto','llm','user')", name="ck_conv_title_source"),
        sa.CheckConstraint("mode IN ('sites','model')", name="ck_conv_mode"),
    )
    op.create_index(
        "ix_conversations_user_updated", "conversations", ["user_id", "updated_at"]
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(10)),
        sa.Column("error", sa.Text()),
        sa.Column("sources", postgresql.JSONB()),
        sa.Column("queries", postgresql.JSONB()),
        sa.Column("meta", postgresql.JSONB()),
        sa.Column("intent", sa.String(30)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("first_token_ms", sa.Integer()),
        sa.Column("not_found", sa.Boolean()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_messages_role"),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
    op.drop_table("conversations")

    op.execute("DROP INDEX IF EXISTS uq_ds_user_name")
    op.drop_index("ix_data_sources_user_id", table_name="data_sources")
    op.drop_column("data_sources", "user_id")
    op.create_unique_constraint("data_sources_name_key", "data_sources", ["name"])

    op.execute("DROP INDEX IF EXISTS uq_sites_user_root_url")
    op.drop_index("ix_sites_user_id", table_name="sites")
    op.drop_column("sites", "user_id")
    op.create_unique_constraint("sites_root_url_key", "sites", ["root_url"])

    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")
    op.execute("DROP INDEX IF EXISTS uq_users_username_lower")
    op.drop_column("users", "is_active")
    op.drop_column("users", "name")
    op.drop_column("users", "username")

