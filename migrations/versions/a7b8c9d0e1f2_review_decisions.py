"""review_decisions: the agentic review's reading of uncertain postings, kept apart from the extraction

Revision ID: a7b8c9d0e1f2
Revises: d4d9e0f1a2b3
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "d4d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("posting_id", sa.Integer(), sa.ForeignKey("postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.String(30), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("run_id", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rejected_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_review_posting_field", "review_decisions", ["posting_id", "field"])
    op.create_index("ix_review_decisions_run_id", "review_decisions", ["run_id"])
    op.create_index("ix_review_decisions_created_at", "review_decisions", ["created_at"])
    op.create_index("ix_review_decisions_applied", "review_decisions", ["applied"])


def downgrade() -> None:
    op.drop_index("ix_review_decisions_applied", table_name="review_decisions")
    op.drop_index("ix_review_decisions_created_at", table_name="review_decisions")
    op.drop_index("ix_review_decisions_run_id", table_name="review_decisions")
    op.drop_index("ix_review_posting_field", table_name="review_decisions")
    op.drop_table("review_decisions")
