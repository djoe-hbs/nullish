"""reset ETag/Last-Modified of pages crawled before render tracking

Pages stored before meta.rendered existed may be JavaScript-rendered, yet still carry the static
shell's ETag. A conditional GET then returns 304 for the (unchanged) shell and the page would be
treated as unchanged forever, even when its real content changes. Clearing the validators forces
one full fetch, which detects JS rendering and records meta.rendered for the future.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-25
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE pages SET etag = NULL, last_modified = NULL WHERE NOT (meta ? 'rendered')"
    )


def downgrade() -> None:
    pass  # nothing to restore: validators are re-learned on the next fetch
