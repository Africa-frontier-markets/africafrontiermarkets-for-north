"""Add idempotent Kora refund records.

Revision ID: 008_kora_refunds
Revises: 007_payment_segregation_audit
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008_kora_refunds"
down_revision = "007_payment_segregation_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kora_refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refund_reference", sa.String(length=50), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reason", sa.String(length=200)),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
        sa.Column("psp_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_message", sa.Text()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("refund_reference", name="uq_kora_refunds_refund_reference"),
        sa.UniqueConstraint("idempotency_key", name="uq_kora_refunds_idempotency_key"),
    )
    op.create_index("ix_kora_refunds_transaction_id", "kora_refunds", ["transaction_id"])
    op.create_index("ix_kora_refunds_refund_reference", "kora_refunds", ["refund_reference"])
    op.create_index("ix_kora_refunds_idempotency_key", "kora_refunds", ["idempotency_key"])
    op.create_index("ix_kora_refunds_payment_reference", "kora_refunds", ["payment_reference"])
    op.create_index("ix_kora_refunds_payment_status", "kora_refunds", ["payment_reference", "status"])


def downgrade() -> None:
    op.drop_index("ix_kora_refunds_payment_status", table_name="kora_refunds")
    op.drop_index("ix_kora_refunds_payment_reference", table_name="kora_refunds")
    op.drop_index("ix_kora_refunds_idempotency_key", table_name="kora_refunds")
    op.drop_index("ix_kora_refunds_refund_reference", table_name="kora_refunds")
    op.drop_index("ix_kora_refunds_transaction_id", table_name="kora_refunds")
    op.drop_table("kora_refunds")
