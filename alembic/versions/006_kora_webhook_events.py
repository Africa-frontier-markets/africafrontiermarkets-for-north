"""Add durable Kora webhook idempotency records.

Revision ID: 006_kora_webhook_events
Revises: 005_virtual_accounts_ledger
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006_kora_webhook_events"
down_revision = "005_virtual_accounts_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kora_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="received"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.String(length=255)),
        sa.UniqueConstraint("event_id", name="uq_kora_webhook_events_event_id"),
    )
    op.create_index("ix_kora_webhook_events_status", "kora_webhook_events", ["status"])
    op.create_index("ix_kora_webhook_events_received_at", "kora_webhook_events", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_kora_webhook_events_received_at", table_name="kora_webhook_events")
    op.drop_index("ix_kora_webhook_events_status", table_name="kora_webhook_events")
    op.drop_table("kora_webhook_events")
