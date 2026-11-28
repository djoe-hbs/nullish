"""add rolling conversation memory columns to conversations

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text()))
    op.add_column(
        "conversations", sa.Column("summary_through_message_id", sa.BigInteger())
    )


def downgrade() -> None:
    op.drop_column("conversations", "summary_through_message_id")
    op.drop_column("conversations", "summary")

