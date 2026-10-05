"""company_logos: employer logos for the job cards (radar/logos.py)

Revision ID: a1a6b7c8d9e0
Revises: f0f5a6b7c8d9
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op

revision = "a1a6b7c8d9e0"
down_revision = "f0f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_logos",
        sa.Column("company", sa.String(200), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False, unique=True, index=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=True),
        sa.Column("data", sa.LargeBinary, nullable=True),
        sa.Column("page_url", sa.String(500), nullable=True),
        sa.Column("image_url", sa.String(500), nullable=True),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("company_logos")
