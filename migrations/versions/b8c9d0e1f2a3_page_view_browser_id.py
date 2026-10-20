"""page_view_browser_id: a hash of the consented radar_id cookie on visits (radar/analytics.py `returning`)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("page_views", sa.Column("browser_id", sa.String(16), nullable=True))
    op.create_index("ix_page_views_browser_id", "page_views", ["browser_id"])


def downgrade() -> None:
    op.drop_index("ix_page_views_browser_id", "page_views")
    op.drop_column("page_views", "browser_id")
