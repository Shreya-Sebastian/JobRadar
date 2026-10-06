"""saved_searches: searches a signed-in user saved from Discover, each with its own job alert

Revision ID: d4d9e0f1a2b3
Revises: c3c8d9e0f1a2
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op

revision = "d4d9e0f1a2b3"
down_revision = "c3c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # the app's start-up (radar.db.init_db) may have created it already
    if sa.inspect(op.get_bind()).has_table("saved_searches"):
        return
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("query", sa.String(2000), nullable=False, server_default=""),
        sa.Column("frequency", sa.String(10), nullable=False, server_default="weekly"),
        sa.Column("sent_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_saved_searches_user_id", "saved_searches", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_searches_user_id", "saved_searches")
    op.drop_table("saved_searches")
