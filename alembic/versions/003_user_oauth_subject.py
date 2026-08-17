"""Add a unique OAuth subject for mobile identity exchange.

Revision ID: 003_user_oauth_subject
Revises: 002_broker_account_links
"""

from alembic import op
import sqlalchemy as sa


revision = "003_user_oauth_subject"
down_revision = "002_broker_account_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oauth_subject", sa.String(length=128), nullable=True))
    op.create_index("ix_users_oauth_subject", "users", ["oauth_subject"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_oauth_subject", table_name="users")
    op.drop_column("users", "oauth_subject")
