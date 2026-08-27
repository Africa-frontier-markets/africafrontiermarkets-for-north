"""User onboarding: email OTP challenge and lightweight identity fields.

No identity documents are collected or stored by AFM.

Revision ID: 010_user_onboarding_otp_kyc
Revises: 009_kora_payment_reconciliation
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "010_user_onboarding_otp_kyc"
down_revision = "009_kora_payment_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    for name, column in (
        ("date_of_birth", sa.Column("date_of_birth", sa.String(length=10))),
        ("email_verified_at", sa.Column("email_verified_at", sa.DateTime(timezone=True))),
        ("phone_verified_at", sa.Column("phone_verified_at", sa.DateTime(timezone=True))),
        ("identity_consent_at", sa.Column("identity_consent_at", sa.DateTime(timezone=True))),
    ):
        if name not in user_columns:
            op.add_column("users", column)

    if "user_otp_challenges" not in inspector.get_table_names():
        op.create_table(
        "user_otp_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50)),
        sa.Column("purpose", sa.String(length=32), nullable=False, server_default="signup"),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("user_otp_challenges")}
    if "ix_user_otp_challenges_email" not in existing_indexes:
        op.create_index("ix_user_otp_challenges_email", "user_otp_challenges", ["email"])
    if "ix_user_otp_email_purpose_created" not in existing_indexes:
        op.create_index("ix_user_otp_email_purpose_created", "user_otp_challenges", ["email", "purpose", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_user_otp_email_purpose_created", table_name="user_otp_challenges")
    op.drop_index("ix_user_otp_challenges_email", table_name="user_otp_challenges")
    op.drop_table("user_otp_challenges")
    op.drop_column("users", "identity_consent_at")
    op.drop_column("users", "phone_verified_at")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "date_of_birth")
