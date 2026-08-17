"""Add secure AFM user to Alpaca account links.

Revision ID: 002_broker_account_links
Revises: 0001_initial
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_broker_account_links"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broker_account_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alpaca_account_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_broker_account_links_user_id"),
        sa.UniqueConstraint("alpaca_account_id", name="uq_broker_account_links_alpaca_account_id"),
    )
    op.create_index("ix_broker_account_links_user_id", "broker_account_links", ["user_id"])
    op.create_index("ix_broker_account_links_alpaca_account_id", "broker_account_links", ["alpaca_account_id"])


def downgrade() -> None:
    op.drop_index("ix_broker_account_links_alpaca_account_id", table_name="broker_account_links")
    op.drop_index("ix_broker_account_links_user_id", table_name="broker_account_links")
    op.drop_table("broker_account_links")
