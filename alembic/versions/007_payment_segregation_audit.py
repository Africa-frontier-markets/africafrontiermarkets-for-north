"""Add AFM payment segregation and audit fields.

Revision ID: 007_payment_segregation_audit
Revises: 006_kora_webhook_events
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "007_payment_segregation_audit"
down_revision = "006_kora_webhook_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("ledger_namespace", sa.String(length=64), nullable=False, server_default="afm_payments"))
    op.add_column("transactions", sa.Column("virtual_account_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("transactions", sa.Column("corridor", sa.String(length=64), nullable=True))
    op.add_column("transactions", sa.Column("beneficiary_currency", sa.String(length=3), nullable=True))
    op.add_column("transactions", sa.Column("total_fee_amount", sa.Numeric(precision=19, scale=8), nullable=True, server_default="0"))
    op.create_index("ix_transactions_virtual_account_id", "transactions", ["virtual_account_id"])
    op.create_foreign_key(
        "fk_transactions_virtual_account_id",
        "transactions",
        "virtual_accounts",
        ["virtual_account_id"],
        ["id"],
    )
    op.alter_column("transactions", "ledger_namespace", server_default=None)
    op.alter_column("transactions", "total_fee_amount", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_transactions_virtual_account_id", "transactions", type_="foreignkey")
    op.drop_index("ix_transactions_virtual_account_id", table_name="transactions")
    op.drop_column("transactions", "total_fee_amount")
    op.drop_column("transactions", "beneficiary_currency")
    op.drop_column("transactions", "corridor")
    op.drop_column("transactions", "virtual_account_id")
    op.drop_column("transactions", "ledger_namespace")
