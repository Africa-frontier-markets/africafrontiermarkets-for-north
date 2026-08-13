"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users first — transactions.user_id has a FK to it
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("is_active", sa.String(length=1), nullable=True, server_default="1"),
        sa.Column("kyc_status", sa.String(length=20), nullable=True, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    payment_status = postgresql.ENUM(
        "pending", "processing", "completed", "failed", "refunded", "held",
        name="paymentstatus",
    )
    psp_type = postgresql.ENUM(
        "kora", "fincra", "flutterwave", "stripe", "mtn_momo", "orange_money",
        name="psptype",
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("psp", psp_type, nullable=False),
        sa.Column("psp_transaction_id", sa.String(length=100), nullable=True),
        sa.Column("psp_response", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("amount", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("fee_amount", sa.Numeric(precision=19, scale=8), nullable=True, server_default="0"),
        sa.Column("fee_currency", sa.String(length=3), nullable=True, server_default="USD"),
        sa.Column("net_amount", sa.Numeric(precision=19, scale=8), nullable=True, server_default="0"),
        sa.Column("status", payment_status, nullable=True, server_default="pending"),
        sa.Column("txn_metadata", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("webhook_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_transactions_idempotency_key", "transactions", ["idempotency_key"], unique=True)
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_user_status", "transactions", ["user_id", "status"])
    op.create_index("ix_transactions_created_at", "transactions", ["created_at"])
    op.create_index("ix_transactions_psp_txn", "transactions", ["psp_transaction_id"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.execute("DROP TYPE IF EXISTS paymentstatus")
    op.execute("DROP TYPE IF EXISTS psptype")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
