"""index_notices: the job pages announced to Google's Indexing API (radar/indexing.py)

Revision ID: e9e4f5a6b7c8
Revises: d8d3e4f5a6b7
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "e9e4f5a6b7c8"
down_revision = "d8d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_notices",
        sa.Column("posting_id", sa.Integer, primary_key=True),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("sent_at", sa.DateTime, nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("index_notices")
