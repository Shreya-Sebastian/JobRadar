"""user_board: the application tracker's columns per saved job, synced for signed-in users

Revision ID: b2b7c8d9e0f1
Revises: a1a6b7c8d9e0
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op

revision = "b2b7c8d9e0f1"
down_revision = "a1a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_data", sa.Column("board", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("user_data", "board")
