"""Remove legacy document-based KYC storage.

Revision ID: 011_remove_document_kyc
Revises: 010_user_onboarding_otp_kyc
"""

from alembic import op
import sqlalchemy as sa

revision = "011_remove_document_kyc"
down_revision = "010_user_onboarding_otp_kyc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The table was only introduced by the unreleased onboarding migration in
    # some environments. Keep this migration defensive for controlled rollout.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_kyc_profiles" in inspector.get_table_names():
        op.drop_index("ix_user_kyc_profiles_user_id", table_name="user_kyc_profiles")
        op.drop_table("user_kyc_profiles")


def downgrade() -> None:
    # Deliberately no downgrade path: document storage is not part of the AFM
    # data model. Restoring it would violate the current minimization policy.
    pass
