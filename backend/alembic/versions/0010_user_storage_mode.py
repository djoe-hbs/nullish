"""add storage_mode preference to users

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("storage_mode", sa.String(length=16)))


def downgrade() -> None:
    op.drop_column("users", "storage_mode")

