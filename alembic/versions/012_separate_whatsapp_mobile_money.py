"""Separate WhatsApp OTP identity from Mobile Money routing identity.

Revision ID: 012_sep_whatsapp_momo
Revises: 011_remove_document_kyc
"""

from alembic import op
import sqlalchemy as sa

revision = "012_sep_whatsapp_momo"
down_revision = "011_remove_document_kyc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("whatsapp_phone", sa.String(length=50)))
    op.add_column("users", sa.Column("mobile_money_phone", sa.String(length=50)))
    op.add_column("users", sa.Column("mobile_money_owner_verified_at", sa.DateTime(timezone=True)))

    op.add_column("transactions", sa.Column("mobile_money_phone", sa.String(length=50)))
    op.add_column("transactions", sa.Column("mobile_money_provider_reference", sa.String(length=128)))
    op.add_column("transactions", sa.Column("mobile_money_owner_verified_at", sa.DateTime(timezone=True)))

    op.create_index("ix_transactions_mobile_money_provider_reference", "transactions", ["mobile_money_provider_reference"])


def downgrade() -> None:
    op.drop_index("ix_transactions_mobile_money_provider_reference", table_name="transactions")
    op.drop_column("transactions", "mobile_money_owner_verified_at")
    op.drop_column("transactions", "mobile_money_provider_reference")
    op.drop_column("transactions", "mobile_money_phone")
    op.drop_column("users", "mobile_money_owner_verified_at")
    op.drop_column("users", "mobile_money_phone")
    op.drop_column("users", "whatsapp_phone")
