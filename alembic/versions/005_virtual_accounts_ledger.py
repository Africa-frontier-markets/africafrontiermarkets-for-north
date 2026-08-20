"""Create AFM virtual account and ledger projections.

Revision ID: 005_virtual_accounts_ledger
Revises: 004_merge_initial_heads
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "005_virtual_accounts_ledger"
down_revision = "004_merge_initial_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "virtual_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_virtual_accounts_user_id"),
    )
    op.create_index("ix_virtual_accounts_user_id", "virtual_accounts", ["user_id"])

    op.create_table(
        "virtual_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("virtual_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=28, scale=10), nullable=False, server_default="0"),
        sa.Column("average_cost", sa.Numeric(precision=19, scale=8), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("position_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["virtual_account_id"], ["virtual_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("virtual_account_id", "symbol", name="uq_virtual_positions_account_symbol"),
    )
    op.create_index("ix_virtual_positions_virtual_account_id", "virtual_positions", ["virtual_account_id"])

    op.create_table(
        "virtual_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("virtual_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.String(length=40), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(precision=19, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("symbol", sa.String(length=32)),
        sa.Column("quantity", sa.Numeric(precision=28, scale=10)),
        sa.Column("reference_type", sa.String(length=40)),
        sa.Column("reference_id", sa.String(length=128)),
        sa.Column("description", sa.String(length=255)),
        sa.Column("entry_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("direction IN ('credit', 'debit')", name="ck_virtual_ledger_direction"),
        sa.ForeignKeyConstraint(["virtual_account_id"], ["virtual_accounts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_virtual_ledger_entries_virtual_account_id", "virtual_ledger_entries", ["virtual_account_id"])
    op.create_index("ix_virtual_ledger_account_occurred", "virtual_ledger_entries", ["virtual_account_id", "occurred_at"])
    op.create_index("ix_virtual_ledger_reference", "virtual_ledger_entries", ["reference_type", "reference_id"])


def downgrade() -> None:
    op.drop_index("ix_virtual_ledger_reference", table_name="virtual_ledger_entries")
    op.drop_index("ix_virtual_ledger_account_occurred", table_name="virtual_ledger_entries")
    op.drop_index("ix_virtual_ledger_entries_virtual_account_id", table_name="virtual_ledger_entries")
    op.drop_table("virtual_ledger_entries")
    op.drop_index("ix_virtual_positions_virtual_account_id", table_name="virtual_positions")
    op.drop_table("virtual_positions")
    op.drop_index("ix_virtual_accounts_user_id", table_name="virtual_accounts")
    op.drop_table("virtual_accounts")
