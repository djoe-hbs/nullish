"""live verification: pages.verified_at, sites.site_search_url

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Last time the page was confirmed against the live site (304 or same/new content).
    # fetched_at = last time the content was downloaded; verified_at >= fetched_at.
    op.add_column("pages", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE pages SET verified_at = fetched_at")
    # Optional on-site search, e.g. https://site.com/search?q={query}, used for live discovery.
    op.add_column("sites", sa.Column("site_search_url", sa.Text()))


def downgrade() -> None:
    op.drop_column("sites", "site_search_url")
    op.drop_column("pages", "verified_at")
