"""add approved_at to users for self-registration approval workflow

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-28
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE users SET approved_at = created_at WHERE is_active = true AND approved_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("users", "approved_at")

