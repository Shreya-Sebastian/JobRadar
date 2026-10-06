"""logo_dark: logos drawn in white are shown on a dark plate (radar/logos.py)

Revision ID: c3c8d9e0f1a2
Revises: b2b7c8d9e0f1
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op

revision = "c3c8d9e0f1a2"
down_revision = "b2b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_logos", sa.Column("dark", sa.Boolean, nullable=True))


def downgrade() -> None:
    op.drop_column("company_logos", "dark")
