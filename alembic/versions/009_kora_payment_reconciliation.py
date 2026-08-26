"""Add durable Kora payment reconciliation tasks.

Revision ID: 009_kora_payment_reconciliation
Revises: 008_kora_refunds
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009_kora_payment_reconciliation"
down_revision = "008_kora_refunds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("psp_payment_reference", sa.String(length=128)))
    op.create_index("ix_transactions_psp_payment_reference", "transactions", ["psp_payment_reference"])
    op.create_table(
        "kora_payment_reconciliations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=False),
        sa.Column("transaction_reference", sa.String(length=128)),
        sa.Column("provider_status", sa.String(length=40), nullable=False, server_default="processing"),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="scheduled"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("transaction_id", name="uq_kora_reconciliation_transaction"),
    )
    op.create_index("ix_kora_reconciliation_due", "kora_payment_reconciliations", ["state", "next_attempt_at"])
    op.create_index("ix_kora_reconciliation_payment_reference", "kora_payment_reconciliations", ["payment_reference"])


def downgrade() -> None:
    op.drop_index("ix_kora_reconciliation_payment_reference", table_name="kora_payment_reconciliations")
    op.drop_index("ix_kora_reconciliation_due", table_name="kora_payment_reconciliations")
    op.drop_table("kora_payment_reconciliations")
    op.drop_index("ix_transactions_psp_payment_reference", table_name="transactions")
    op.drop_column("transactions", "psp_payment_reference")
