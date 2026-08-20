"""Production-hardened configuration with Pydantic v2 validation."""

from functools import lru_cache
from typing import List, Literal
"""
AFM Configuration — Africa Frontier Markets
Production-hardened settings. No COSY references.
"""

from functools import lru_cache
from typing import List, Literal
from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.db_url import normalize_database_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Security
    secret_key: str = Field(..., min_length=48)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=30)
    mobile_oauth_exchange_secret: str | None = Field(default=None, min_length=48)
    # Comma-separated AFM user UUIDs allowed to view aggregate virtual-ledger operations.
    # Empty by default: the back-office remains closed until explicitly configured.
    afm_backoffice_admin_subjects: str = ""

    @field_validator("secret_key")
    @classmethod
    def validate_secret_length(cls, v: str) -> str:
        if len(v) < 48:
            raise ValueError(f"SECRET_KEY must be >=48 characters, got {len(v)}")
        return v

    database_url: str = Field(..., pattern=r"^postgresql\+asyncpg://")
    # Database
    # 🟢 RENDER COMPAT: Render (and Heroku-style platforms) inject
    # DATABASE_URL as postgresql://..., not postgresql+asyncpg://. Accept
    # both; normalize_database_url() rewrites the scheme for SQLAlchemy.
    database_url: str = Field(..., pattern=r"^postgres(ql)?(\+asyncpg)?://")
    db_pool_size: int = Field(default=20, ge=5, le=100)
    db_max_overflow: int = Field(default=30, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=5, le=120)

    redis_url: str = Field(..., pattern=r"^rediss?://")
    redis_pool_size: int = Field(default=50, ge=10, le=200)

    # Redis
    redis_url: str = Field(..., pattern=r"^rediss?://")
    redis_pool_size: int = Field(default=50, ge=10, le=200)

    # Payment Providers
    kora_api_key: str | None = None
    kora_secret_key: str | None = None
    kora_encryption_key: str | None = None
    fincra_api_key: str | None = None
    fincra_secret_key: str | None = None
    flutterwave_public_key: str | None = None
    flutterwave_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_secret_key: str | None = None

    kora_webhook_secret: str | None = None
    fincra_webhook_secret: str | None = None

    # Legacy Alpaca Broker API — retained temporarily for existing direct-account routes.
    alpaca_api_key: str | None = None
    alpaca_secret_key: str | None = None
    alpaca_paper: bool = True
    alpaca_broker_base_url: str = "https://broker-api.sandbox.alpaca.markets"
    alpaca_auth_url: str = "https://authx.sandbox.alpaca.markets/v1/oauth2/token"
    alpaca_market_data_base_url: str = "https://data.sandbox.alpaca.markets"

    # Alpaca Trading API — dedicated key pair, never shared with Broker OAuth credentials.
    alpaca_trading_api_key: str | None = None
    alpaca_trading_api_secret: str | None = None
    # AFM uses a dedicated Live Trading API key pair; write operations remain
    # blocked by the Trading API client until separately approved.
    alpaca_trading_base_url: str = "https://api.alpaca.markets"
    alpaca_trading_market_data_base_url: str = "https://data.alpaca.markets"

    # Webhook Secrets
    kora_webhook_secret: str | None = None
    fincra_webhook_secret: str | None = None

    # Environment
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_logs: bool = True

    allowed_origins: str = "http://localhost:3000"

    ads_share: float = Field(default=0.35, ge=0.0, le=1.0)
    creator_share: float = Field(default=0.50, ge=0.0, le=1.0)
    platform_share: float = Field(default=0.15, ge=0.0, le=1.0)

    enable_copy_trading: bool = True
    enable_social_trading: bool = True
    max_copy_allocation_percent: int = Field(default=100, ge=1, le=100)
    # CORS
    allowed_origins: str = "http://localhost:3000"

    # AFM B2B Revenue Model: commission per transaction (bps)
    afm_commission_bps: int = Field(default=25, ge=0, le=500)  # 0.25% per txn
    afm_min_fee_usd: Decimal = Field(default=Decimal("0.50"))
    afm_max_fee_usd: Decimal = Field(default=Decimal("50.00"))

    # Trading Limits
    max_order_value_usd: Decimal = Field(default=Decimal("10000"), ge=Decimal("100"))
    max_user_exposure_usd: Decimal = Field(default=Decimal("50000"), ge=Decimal("1000"))
    max_platform_exposure_usd: Decimal = Field(default=Decimal("400000"), ge=Decimal("10000"))

    # FX Spread
    fx_spread_bps: int = Field(default=50, ge=0, le=500)

    def get_allowed_origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def resolved_database_url(self) -> str:
        """DATABASE_URL rewritten to the postgresql+asyncpg:// scheme
        SQLAlchemy needs, regardless of which scheme was provided."""
        normalized, _ = normalize_database_url(self.database_url)
        return normalized

    @property
    def db_ssl_required(self) -> bool:
        """True if the raw DATABASE_URL requested sslmode=require (or
        stricter) — asyncpg needs this passed as a connect_arg, not a
        query param."""
        _, ssl_required = normalize_database_url(self.database_url)
        return ssl_required

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_staging(self) -> bool:
        return self.environment == "staging"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Backwards-compatible module-level settings used by integrations.
settings = get_settings()
