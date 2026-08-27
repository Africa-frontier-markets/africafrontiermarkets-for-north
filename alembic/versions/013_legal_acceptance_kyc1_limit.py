"""Add versioned legal acceptance metadata and KYC1 policy configuration.

Revision ID: 013_legal_acceptance_kyc1_limit
Revises: 012_sep_whatsapp_momo
"""

from alembic import op
import sqlalchemy as sa

revision = "013_legal_acceptance_kyc1_limit"
down_revision = "012_sep_whatsapp_momo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("terms_version", sa.String(length=32)))
    op.add_column("users", sa.Column("aml_policy_version", sa.String(length=32)))


def downgrade() -> None:
    op.drop_column("users", "aml_policy_version")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")
