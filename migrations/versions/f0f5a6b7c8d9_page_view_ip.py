"""page_view_ip: the visitor's IP address on page views, kept 30 days (radar/analytics.py)

Revision ID: f0f5a6b7c8d9
Revises: e9e4f5a6b7c8
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "f0f5a6b7c8d9"
down_revision = "e9e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("page_views", sa.Column("ip", sa.String(45), nullable=True))


def downgrade() -> None:
    op.drop_column("page_views", "ip")
