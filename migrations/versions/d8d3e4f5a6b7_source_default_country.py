"""sources.default_country: the country for postings that name no location (a Dutch employer's own board)

Revision ID: d8d3e4f5a6b7
Revises: c7c2d3e4f5a6
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "d8d3e4f5a6b7"
down_revision = "c7c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("default_country", sa.String(2), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "default_country")
