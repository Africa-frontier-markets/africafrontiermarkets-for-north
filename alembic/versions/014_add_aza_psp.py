"""Add AZA Finance to the PSP enum."""
from alembic import op

revision = "014_add_aza_psp"
down_revision = "013_legal_acceptance_kyc1_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE psptype ADD VALUE IF NOT EXISTS 'aza'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value safely in-place. Keep the value
    # for forward-compatible deployments; the application no longer uses it
    # after a downgrade.
    pass
