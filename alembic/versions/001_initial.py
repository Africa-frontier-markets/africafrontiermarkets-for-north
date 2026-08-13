"""Initial migration - Africa Frontier Markets schema.

Revision ID: 001_initial
Revises:
Create Date: 2026-07-06 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Markets
    op.create_table(
        'markets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('code', sa.String(10), nullable=False, unique=True, index=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('country', sa.String(2), nullable=False, index=True),
        sa.Column('timezone', sa.String(50), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('open_time', sa.String(5), nullable=False),
        sa.Column('close_time', sa.String(5), nullable=False),
        sa.Column('weekend_days', sa.String(20), default='6,7'),
        sa.Column('status', sa.Enum('open', 'closed', 'halted', 'pre_market', 'after_hours', name='marketstatus'), default='closed'),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_markets_active_country', 'markets', ['is_active', 'country'])

    # Brokers
    op.create_table(
        'brokers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('api_endpoint', sa.String(255), nullable=False),
        sa.Column('supported_markets', sa.String(500), nullable=False),
        sa.Column('supported_order_types', sa.String(200), default='["market","limit"]'),
        sa.Column('latency_ms', sa.Float, default=100.0),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('priority', sa.Float, default=1.0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Users
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Platforms
    op.create_table(
        'platforms',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('contact_email', sa.String(255), nullable=False),
        sa.Column('webhook_url', sa.String(500)),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # API Keys
    op.create_table(
        'api_keys',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('platform_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('platforms.id'), nullable=False),
        sa.Column('key_value', sa.String(100), unique=True, nullable=False),
        sa.Column('secret_hash', sa.String(255), nullable=False),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
    )

    # Wallets
    op.create_table(
        'wallets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False, unique=True),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('balance', sa.Numeric(19, 8), default=0),
        sa.Column('locked_balance', sa.Numeric(19, 8), default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Wallet Transactions
    op.create_table(
        'wallet_transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('wallet_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('wallets.id'), nullable=False),
        sa.Column('type', sa.Enum('deposit', 'withdrawal', 'transfer_in', 'transfer_out', name='wallettransactiontype'), nullable=False),
        sa.Column('amount', sa.Numeric(19, 8), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('reference', sa.String(255)),
        sa.Column('status', sa.String(50), default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Copy Trades
    op.create_table(
        'copy_trades',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('follower_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('leader_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('allocation_percent', sa.Integer, nullable=False),
        sa.Column('max_drawdown', sa.Numeric(5, 2), default=20.0),
        sa.Column('status', sa.Enum('active', 'paused', 'stopped', name='copystatus'), default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('stopped_at', sa.DateTime(timezone=True)),
    )

    # Transactions (Payments)
    op.create_table(
        'transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('idempotency_key', sa.String(64), unique=True, nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('psp', sa.Enum('kora', 'fincra', 'flutterwave', 'stripe', name='psptype'), nullable=False),
        sa.Column('psp_transaction_id', sa.String(100)),
        sa.Column('amount', sa.Numeric(19, 8), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('status', sa.Enum('pending', 'processing', 'completed', 'failed', 'refunded', name='paymentstatus'), default='pending'),
        sa.Column('metadata', sa.JSON, default={}),
        sa.Column('error_message', sa.String(500)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('ix_transactions_user_status', 'transactions', ['user_id', 'status'])
    op.create_index('ix_transactions_created_at', 'transactions', ['created_at'])

    # Revenue Records
    op.create_table(
        'revenue_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text('uuid_generate_v4()')),
        sa.Column('transaction_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('transactions.id'), nullable=False),
        sa.Column('gross_amount', sa.Numeric(19, 8), nullable=False),
        sa.Column('ads_amount', sa.Numeric(19, 8), nullable=False),
        sa.Column('creator_amount', sa.Numeric(19, 8), nullable=False),
        sa.Column('platform_amount', sa.Numeric(19, 8), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('revenue_records')
    op.drop_table('transactions')
    op.drop_table('copy_trades')
    op.drop_table('wallet_transactions')
    op.drop_table('wallets')
    op.drop_table('api_keys')
    op.drop_table('platforms')
    op.drop_table('users')
    op.drop_table('brokers')
    op.drop_table('markets')
    op.drop_index('ix_markets_active_country')
    op.drop_index('ix_transactions_user_status')
    op.drop_index('ix_transactions_created_at')
