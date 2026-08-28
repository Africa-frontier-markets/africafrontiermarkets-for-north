"""
AFM API Gateway — FastAPI with real payment endpoint
"""

import asyncio
import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator

from pydantic import BaseModel, Field
from fastapi import FastAPI, Request, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, or_, select, func, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.config import get_settings
from config.database import init_db, engine, get_db, AsyncSessionLocal
from config.exceptions import (
    AFMException, ValidationError, NotFoundError,
)
from config.logging_config import configure_logging
from config.rate_limit import rate_limiter
from config.security import (
    decode_token,
    get_current_backoffice_admin_id,
    get_current_user_id,
    create_access_token,
    verify_kora_webhook_signature,
)
from config.telemetry import app_info, http_requests_total, http_request_duration, get_metrics_response, CONTENT_TYPE_LATEST
from event_bus.redis_producer import event_producer
from event_bus.event_schema import BaseEvent, EventType
from payment_hub.payment_service import payment_service
from payment_hub.kora_client import KoraClient, KoraClientError, get_kora_balance
from payment_hub.reconciliation import enqueue_reconciliation, reconciliation_worker
from payment_hub.support_ai import analyze_incident, enrich_with_external_llm
from payment_hub.models import (
    BrokerAccountLink,
    KoraWebhookEvent,
    KoraRefund,
    Transaction,
    PaymentStatus,
    PSPType,
    VirtualAccount,
    VirtualLedgerEntry,
    VirtualPosition,
    User,
)
from api_gateway.auth import router as auth_router
from api_gateway.kora_alerts import notify_kora_failure

logger = configure_logging()
_instrument_cache: dict[str, tuple[float, list[dict]]] = {}
_INSTRUMENT_CACHE_TTL_SECONDS = 300
_market_history_cache: dict[str, tuple[float, dict]] = {}
_MARKET_HISTORY_CACHE_TTL_SECONDS = 60
PUBLIC_MARKET_SYMBOLS = ("AAPL", "MSFT", "NVDA", "BTC/USD", "ETH/USD")

class TransactionType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"
    PAYMENT = "payment"
    TRADE = "trade"
    COPY_TRADE = "copy_trade"
    FEE = "fee"
    REVENUE_SPLIT = "revenue_split"

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    settings = get_settings()
    app_info.info({
        "version": "prod-1.0.0",
        "environment": settings.environment,
    })
    logger.info("Starting AFM API", environment=settings.environment)
    await init_db()
    reconciliation_stop = asyncio.Event()
    reconciliation_task = asyncio.create_task(
        reconciliation_worker(reconciliation_stop, AsyncSessionLocal)
    )
    try:
        yield
    finally:
        reconciliation_stop.set()
        reconciliation_task.cancel()
        with suppress(asyncio.CancelledError):
            await reconciliation_task
        logger.info("Shutting down AFM API")
        await payment_service.close()
    await event_producer.close()
    await rate_limiter.close()
    await engine.dispose()

app = FastAPI(
    title="Africa Frontier Markets API",
    description="B2B Fintech API for African payment corridors and US equity trading",
    version="prod-1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if get_settings().is_development else None,
    redoc_url="/redoc" if get_settings().is_development else None,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(auth_router)


async def require_verified_user(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """Production transfer gate: verified account identity; provider checks each Mobile Money transaction."""
    if not get_settings().is_production:
        return user_id
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.is_active != "1":
        raise HTTPException(status_code=401, detail="User account is unavailable")
    if not user.email_verified_at:
        raise HTTPException(status_code=403, detail="Email verification is required before transfers")
    settings = get_settings()
    if not user.terms_accepted_at or user.terms_version != settings.terms_version or user.aml_policy_version != settings.aml_policy_version:
        raise HTTPException(status_code=403, detail="Acceptance of the current terms and AML policy is required before transfers")
    return user_id


async def enforce_kyc1_monthly_limit(*, user_id: uuid.UUID, amount: Decimal, currency: str, db: AsyncSession) -> None:
    """Apply the internal AFM KYC1 monthly cap to XAF/XOF payment flows.

    The cap is an AFM policy control, not a replacement for operator/Kora limits.
    Non-CFA corridors are handled by the higher-tier corridor policy.
    """
    if not get_settings().is_production:
        return
    normalized_currency = currency.upper()
    if normalized_currency not in {"XAF", "XOF"}:
        raise HTTPException(status_code=403, detail="This corridor requires enhanced verification")
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    excluded = [PaymentStatus.FAILED, PaymentStatus.REFUNDED]
    total = (await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.ledger_namespace == "afm_payments",
            Transaction.currency.in_(["XAF", "XOF"]),
            Transaction.created_at >= month_start,
            Transaction.status.notin_(excluded),
        )
    )).scalar_one()
    proposed_total = Decimal(str(total or 0)) + amount
    limit = Decimal(str(get_settings().kyc1_monthly_limit_xaf))
    if proposed_total > limit:
        raise HTTPException(status_code=403, detail="Monthly KYC1 limit exceeded; enhanced verification is required")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Idempotency-Key", "X-AFM-Payout-Confirmation", "X-AFM-Refund-Confirmation"],
    max_age=600,
)

@app.exception_handler(AFMException)
async def afm_exception_handler(request: Request, exc: AFMException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": exc.error_code, "detail": exc.detail},
    )

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now(timezone.utc)
    request_id = request.headers.get("X-Request-ID", "unknown")

    import structlog
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)

    response = await call_next(request)

    duration = (datetime.now(timezone.utc) - start).total_seconds()
    http_request_duration.labels(
        method=request.method,
        endpoint=request.url.path,
    ).observe(duration)
    http_requests_total.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code,
    ).inc()

    logger.info(
        "Request completed",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration * 1000, 2),
    )

    return response

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.head("/health")
async def health_head():
    return Response(status_code=200)

@app.get("/ready")
async def readiness_check():
    checks = {
        "database": await _check_database(),
        "redis": await _check_redis(),
    }
    all_ready = all(checks.values())

    if not all_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "checks": checks},
        )

    return {"status": "ready", "checks": checks}

@app.head("/ready")
async def readiness_head():
    return Response(status_code=200)

async def _check_database() -> bool:
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

async def _check_redis() -> bool:
    try:
        import redis.asyncio as redis
        client = redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        await client.close()
        return True
    except Exception:
        return False

@app.get("/metrics")
async def metrics():
    return PlainTextResponse(
        content=get_metrics_response(),
        media_type=CONTENT_TYPE_LATEST,
    )

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


@app.get("/")
async def root():
    index_path = PUBLIC_DIR / "afm-web.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {
        "name": "Africa Frontier Markets",
        "version": "prod-1.0.0",
        "status": "operational",
    }


@app.get("/onboarding")
async def onboarding_page():
    onboarding_path = PUBLIC_DIR / "onboarding.html"
    if onboarding_path.exists():
        return FileResponse(onboarding_path)
    raise HTTPException(status_code=404, detail="Onboarding page not found")


@app.get("/terms")
async def terms_page():
    terms_path = PUBLIC_DIR / "terms.html"
    if terms_path.exists():
        return FileResponse(terms_path)
    raise HTTPException(status_code=404, detail="Terms page not found")


@app.get("/aml-policy")
async def aml_policy_page():
    aml_path = PUBLIC_DIR / "aml-policy.html"
    if aml_path.exists():
        return FileResponse(aml_path)
    raise HTTPException(status_code=404, detail="AML policy page not found")


@app.get("/legal/versions")
async def legal_versions():
    settings = get_settings()
    return {
        "terms_version": settings.terms_version,
        "aml_policy_version": settings.aml_policy_version,
        "terms_url": "/terms",
        "aml_policy_url": "/aml-policy",
    }


@app.get("/contact/loic-mpanjo")
async def loic_mpanjo_contact_page():
    contact_path = PUBLIC_DIR / "loic-mpanjo.html"
    if contact_path.exists():
        return FileResponse(contact_path)
    raise HTTPException(status_code=404, detail="Contact page not found")


@app.get("/sandbox")
async def sandbox_page():
    sandbox_path = PUBLIC_DIR / "sandbox.html"
    if sandbox_path.exists():
        return FileResponse(sandbox_path)
    raise HTTPException(status_code=404, detail="Sandbox page not found")


@app.get("/changelog")
async def changelog_page():
    changelog_path = PUBLIC_DIR / "changelog.html"
    if changelog_path.exists():
        return FileResponse(changelog_path)
    raise HTTPException(status_code=404, detail="Changelog page not found")


@app.get("/markets")
@app.get("/compliance")
async def public_web_pages():
    """Serve the standalone public web experience without involving the mobile app."""
    index_path = PUBLIC_DIR / "afm-web.html"
    if index_path.exists():
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Public web frontend not found")


@app.head("/")
async def root_head():
    return Response(status_code=200)


@app.get("/dashboard")
async def dashboard():
    dashboard_path = PUBLIC_DIR / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    raise HTTPException(status_code=404, detail="Dashboard not found")


if PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")

# --------------------------------------------------------------------------
# Alpaca Broker API integration
# --------------------------------------------------------------------------

@app.get("/api/v1/broker/status")
async def broker_status():
    """Report whether Alpaca Broker credentials are configured and valid."""
    from market_gateway.alpaca_broker import alpaca_broker_client

    if not alpaca_broker_client.configured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"configured": False, "detail": "Alpaca credentials missing"},
        )
    try:
        await alpaca_broker_client.token()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"configured": True, "authenticated": False, "detail": str(exc)},
        )
    return {
        "configured": True,
        "authenticated": True,
        "base_url": alpaca_broker_client.base_url,
        "paper": settings.alpaca_paper,
    }

@app.get("/api/v1/broker/accounts")
async def broker_list_accounts():
    """List accounts from the Alpaca Broker API (sandbox)."""
    from market_gateway.alpaca_broker import alpaca_broker_client

    try:
        accounts = await alpaca_broker_client.list_accounts()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    return {"accounts": accounts}

class BrokerAccountCreateRequest(BaseModel):
    """Payload forwarded to Alpaca Broker account creation in sandbox only."""
    contact: dict
    identity: dict
    disclosures: dict
    agreements: list[dict] = Field(default_factory=list)


class BrokerOrderRequest(BaseModel):
    """Minimal order payload for sandbox validation."""
    symbol: str = Field(..., min_length=1, max_length=16)
    qty: str = Field(default="1")
    side: str = Field(default="buy", pattern="^(buy|sell)$")
    type: str = Field(default="market", pattern="^(market|limit|stop|stop_limit|trailing_stop)$")
    time_in_force: str = Field(default="day", pattern="^(day|gtc|opg|cls|ioc|fok)$")
    limit_price: str | None = None
    stop_price: str | None = None
    client_order_id: str | None = None


@app.post("/api/v1/broker/accounts", status_code=status.HTTP_201_CREATED)
async def broker_create_account(payload: BrokerAccountCreateRequest):
    """Create a Broker sandbox account; disabled automatically outside paper mode."""
    if not settings.alpaca_paper:
        raise HTTPException(status_code=403, detail="Account creation is sandbox-only")
    from market_gateway.alpaca_broker import alpaca_broker_client
    try:
        return await alpaca_broker_client.create_account(payload.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/v1/broker/accounts/{account_id}/orders", status_code=status.HTTP_201_CREATED)
async def broker_create_order(account_id: str, order: BrokerOrderRequest):
    """Submit an order to the Alpaca Broker sandbox only."""
    if not settings.alpaca_paper:
        raise HTTPException(status_code=403, detail="Orders are sandbox-only")
    from market_gateway.alpaca_broker import alpaca_broker_client
    try:
        return await alpaca_broker_client.create_order(
            account_id,
            order.model_dump(exclude_none=True),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/v1/broker/accounts/{account_id}")
async def broker_get_account(account_id: str):
    """Read one Broker sandbox account."""
    from market_gateway.alpaca_broker import alpaca_broker_client
    try:
        return await alpaca_broker_client.get_account(account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/v1/broker/accounts/{account_id}/orders")
async def broker_list_orders(account_id: str):
    """List orders for one Broker sandbox account."""
    from market_gateway.alpaca_broker import alpaca_broker_client
    try:
        return await alpaca_broker_client.list_orders(account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


async def get_tradable_trading_assets(asset_class: str | None = None) -> list[dict]:
    """Fetch and cache active, tradable assets from the read-only Trading API."""
    from market_gateway.alpaca_trading import alpaca_trading_client

    cache_key = asset_class or "all"
    cached = _instrument_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _INSTRUMENT_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        params = {"status": "active"}
        if asset_class:
            params["asset_class"] = asset_class
        assets = await alpaca_trading_client.list_assets(**params)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    tradable_assets = [asset for asset in assets if asset.get("tradable") is True] if isinstance(assets, list) else []
    tradable_assets.sort(key=lambda asset: str(asset.get("symbol") or ""))
    _instrument_cache[cache_key] = (time.monotonic(), tradable_assets)
    return tradable_assets


def normalize_bars(symbol: str, bars: list[dict]) -> dict:
    """Convert Alpaca OHLC bars to a minimal chart-safe public payload."""
    normalized = [
        {
            "timestamp": bar.get("t"),
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
        }
        for bar in bars
        if isinstance(bar, dict) and isinstance(bar.get("c"), (int, float))
    ]
    closes = [float(bar["close"]) for bar in normalized]
    latest = closes[-1] if closes else None
    previous = closes[-2] if len(closes) > 1 else None
    change = latest - previous if latest is not None and previous not in (None, 0) else None
    return {
        "symbol": symbol,
        "bars": normalized,
        "last_price": latest,
        "previous_close": previous,
        "change": change,
        "change_percent": (change / previous * 100) if change is not None and previous else None,
        "currency": "USD",
    }


async def get_market_histories(
    symbols: list[str],
    days: int = 7,
    timeframe: str = "1Day",
) -> dict[str, dict]:
    """Fetch cached daily Trading API bars for a bounded equity/crypto batch."""
    clean_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})[:25]
    if not clean_symbols:
        return {}
    cache_key = f"{'|'.join(clean_symbols)}:{days}:{timeframe}"
    cached = _market_history_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _MARKET_HISTORY_CACHE_TTL_SECONDS:
        return cached[1]
    from market_gateway.alpaca_trading import alpaca_trading_client

    start = (datetime.now(timezone.utc) - timedelta(days=days + 4)).isoformat()
    stock_symbols = [symbol for symbol in clean_symbols if "/" not in symbol]
    crypto_symbols = [symbol for symbol in clean_symbols if "/" in symbol]
    limit = min(1000, len(clean_symbols) * (days * (8 if timeframe == "1Hour" else 1) + 8))
    requests = []
    if stock_symbols:
        requests.append(alpaca_trading_client.get_stock_bars(
            stock_symbols,
            timeframe=timeframe,
            start=start,
            adjustment="all",
            feed="iex",
            limit=limit,
        ))
    if crypto_symbols:
        requests.append(alpaca_trading_client.get_crypto_bars(
            crypto_symbols,
            timeframe=timeframe,
            start=start,
            limit=limit,
        ))
    try:
        payloads = await asyncio.gather(*requests)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to load Trading API market data: {exc}")
    bars_by_symbol: dict[str, list[dict]] = {}
    for payload in payloads:
        if isinstance(payload, dict):
            bars_by_symbol.update(payload.get("bars", {}))
    histories = {
        symbol: normalize_bars(symbol, bars_by_symbol.get(symbol, [])[-days:])
        for symbol in clean_symbols
    }
    _market_history_cache[cache_key] = (time.monotonic(), histories)
    return histories


def paginate_instruments(assets: list[dict], query: str, page: int, page_size: int) -> tuple[list[dict], int]:
    normalized_query = query.strip().lower()
    if normalized_query:
        assets = [
            asset for asset in assets
            if normalized_query in str(asset.get("symbol") or "").lower()
            or normalized_query in str(asset.get("name") or "").lower()
            or normalized_query in str(asset.get("exchange") or "").lower()
        ]
    total_count = len(assets)
    offset = (page - 1) * page_size
    return assets[offset : offset + page_size], total_count


@app.get("/api/v1/trading/assets")
async def trading_list_assets(
    asset_class: str | None = Query(default=None, max_length=50),
    query: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """List active, tradable Trading API instruments through a filtered, paginated response."""
    tradable_assets = await get_tradable_trading_assets(asset_class)
    assets, total_count = paginate_instruments(tradable_assets, query, page, page_size)
    return {
        "count": len(assets),
        "total_count": total_count,
        "asset_class": asset_class or "all",
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total_count,
        "assets": assets,
    }


@app.get("/api/v1/broker/assets", include_in_schema=False)
async def legacy_broker_list_assets(
    asset_class: str | None = Query(default=None, max_length=50),
    query: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """Legacy alias retained temporarily for existing clients."""
    return await trading_list_assets(asset_class, query, page, page_size)


@app.get("/api/v1/trading/instruments")
async def trading_list_instruments(
    asset_class: str | None = Query(default=None, max_length=50),
    query: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    kind: str | None = Query(default=None, max_length=50, include_in_schema=False),
    limit: int | None = Query(default=None, ge=1, le=100, include_in_schema=False),
):
    """Frontend-friendly Trading API instrument list with legacy read-query aliases."""
    return await trading_list_assets(
        asset_class=asset_class or kind,
        query=query,
        page=page,
        page_size=limit or page_size,
    )


@app.get("/api/v1/public/market-products")
async def public_market_products():
    """Return five verified instruments, including supported crypto pairs, without order actions."""
    tradable_assets = await get_tradable_trading_assets()
    assets_by_symbol = {str(asset.get("symbol")): asset for asset in tradable_assets}
    selected_assets = [assets_by_symbol[symbol] for symbol in PUBLIC_MARKET_SYMBOLS if symbol in assets_by_symbol]
    snapshots = await get_market_histories([str(asset["symbol"]) for asset in selected_assets], days=7)
    return {
        "count": len(selected_assets),
        "symbols": list(PUBLIC_MARKET_SYMBOLS),
        "assets": selected_assets,
        "snapshots": snapshots,
        "orders_enabled": False,
    }


@app.get("/api/v1/broker/instruments", include_in_schema=False)
async def legacy_broker_list_instruments(
    asset_class: str | None = Query(default=None, max_length=50),
    query: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """Legacy alias retained temporarily for existing clients."""
    return await trading_list_instruments(asset_class, query, page, page_size)


@app.get("/api/v1/trading/market-snapshots")
async def trading_market_snapshots(
    symbols: str = Query(..., min_length=1, max_length=500),
):
    """Return bounded, cache-backed Trading API price snapshots for catalogue sparklines."""
    requested = [symbol for symbol in symbols.split(",") if symbol.strip()]
    histories = await get_market_histories(requested, days=7)
    return {"snapshots": histories}


@app.get("/api/v1/trading/snapshots", include_in_schema=False)
async def legacy_trading_snapshots(
    symbols: str = Query(..., min_length=1, max_length=500),
):
    """Read-only compatibility alias for market snapshot clients."""
    return await trading_market_snapshots(symbols)


@app.get("/api/v1/broker/market-snapshots", include_in_schema=False)
async def legacy_broker_market_snapshots(
    symbols: str = Query(..., min_length=1, max_length=500),
):
    """Legacy alias retained temporarily for existing clients."""
    return await trading_market_snapshots(symbols)


@app.get("/api/v1/broker/instruments/{symbol}/history")
async def broker_instrument_history(
    symbol: str,
    period: str = Query(default="1M", pattern="^(1D|1W|1M|1Y)$"),
):
    """Return historical Alpaca daily bars for one market instrument. No order is created."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol.replace("-", "").replace(".", "").replace("/", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid market symbol")
    periods = {
        "1D": {"days": 1, "timeframe": "1Hour"},
        "1W": {"days": 7, "timeframe": "1Hour"},
        "1M": {"days": 30, "timeframe": "1Day"},
        "1Y": {"days": 365, "timeframe": "1Day"},
    }
    config = periods[period]
    history = await get_market_histories([normalized_symbol], **config)
    return history.get(normalized_symbol, normalize_bars(normalized_symbol, []))


@app.get("/api/v1/trading/instruments/{symbol:path}/history")
async def trading_instrument_history(
    symbol: str,
    period: str = Query(default="1M", pattern="^(1D|1W|1M|1Y)$"),
):
    """Read-only Trading API history route for mobile and public market clients."""
    return await broker_instrument_history(symbol, period)


def virtual_account_public_id(account: VirtualAccount) -> str:
    """Return a stable display reference without exposing the database UUID."""
    return f"AFM-VIRTUAL-{str(account.id).split('-')[0].upper()}"


async def get_or_create_virtual_account(user_id: uuid.UUID, db: AsyncSession) -> VirtualAccount:
    result = await db.execute(select(VirtualAccount).where(VirtualAccount.user_id == user_id))
    account = result.scalar_one_or_none()
    if account is not None:
        return account

    account = VirtualAccount(user_id=user_id, status="active", currency="USD")
    db.add(account)
    try:
        await db.commit()
        await db.refresh(account)
        return account
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(VirtualAccount).where(VirtualAccount.user_id == user_id))
        existing = result.scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=503, detail="Virtual account provisioning is temporarily unavailable")
        return existing


async def get_virtual_cash_balance(account_id: uuid.UUID, db: AsyncSession) -> Decimal:
    result = await db.execute(
        select(VirtualLedgerEntry.direction, VirtualLedgerEntry.amount).where(
            VirtualLedgerEntry.virtual_account_id == account_id,
        ),
    )
    balance = Decimal("0")
    for direction, amount in result.all():
        signed_amount = Decimal(str(amount or 0))
        balance += signed_amount if direction == "credit" else -signed_amount
    return balance


async def build_virtual_portfolio(account: VirtualAccount, db: AsyncSession) -> dict:
    result = await db.execute(
        select(VirtualPosition).where(VirtualPosition.virtual_account_id == account.id).order_by(VirtualPosition.symbol),
    )
    virtual_positions = result.scalars().all()
    histories = await get_market_histories([position.symbol for position in virtual_positions], days=7) if virtual_positions else {}
    positions: list[dict] = []
    total_market_value = Decimal("0")
    total_unrealized_pl = Decimal("0")

    for position in virtual_positions:
        quantity = Decimal(str(position.quantity or 0))
        average_cost = Decimal(str(position.average_cost or 0))
        snapshot = histories.get(position.symbol, {})
        last_price = snapshot.get("last_price")
        current_price = Decimal(str(last_price)) if last_price is not None else None
        market_value = quantity * current_price if current_price is not None else None
        cost_basis = quantity * average_cost
        unrealized_pl = market_value - cost_basis if market_value is not None else None
        if market_value is not None:
            total_market_value += market_value
        if unrealized_pl is not None:
            total_unrealized_pl += unrealized_pl
        positions.append({
            "asset_id": str(position.id),
            "symbol": position.symbol,
            "asset_class": "crypto" if "/" in position.symbol else "us_equity",
            "qty": str(quantity),
            "market_value": str(market_value) if market_value is not None else None,
            "cost_basis": str(cost_basis),
            "unrealized_pl": str(unrealized_pl) if unrealized_pl is not None else None,
            "unrealized_plpc": str(unrealized_pl / cost_basis) if unrealized_pl is not None and cost_basis else None,
            "current_price": str(current_price) if current_price is not None else None,
        })

    cash = await get_virtual_cash_balance(account.id, db)
    equity = cash + total_market_value
    return {
        "account_id": virtual_account_public_id(account),
        "account": {
            "id": virtual_account_public_id(account),
            "account_number": virtual_account_public_id(account),
            "status": account.status,
            "currency": account.currency,
            "cash": str(cash),
            "equity": str(equity),
            "buying_power": str(max(cash, Decimal("0"))),
            "portfolio_value": str(equity),
            "unrealized_pl": str(total_unrealized_pl),
        },
        "positions": positions,
        "reconciliation": {"status": "pending", "last_reconciled_at": None},
        "read_only": True,
    }


@app.get("/api/v1/trading/portfolio")
async def get_virtual_portfolio(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated AFM user’s virtual-account projection without an order action."""
    account = await get_or_create_virtual_account(user_id, db)
    return await build_virtual_portfolio(account, db)


@app.get("/api/v1/trading/portfolio/transactions")
async def get_virtual_portfolio_transactions(
    limit: int = Query(default=50, ge=1, le=100),
    page_token: str | None = Query(default=None, min_length=1, max_length=64),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return immutable virtual-ledger entries for the authenticated virtual account."""
    account = await get_or_create_virtual_account(user_id, db)
    statement = select(VirtualLedgerEntry).where(VirtualLedgerEntry.virtual_account_id == account.id)
    if page_token:
        try:
            token_id = uuid.UUID(page_token)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid transaction page token")
        token_result = await db.execute(
            select(VirtualLedgerEntry).where(
                VirtualLedgerEntry.id == token_id,
                VirtualLedgerEntry.virtual_account_id == account.id,
            ),
        )
        token_entry = token_result.scalar_one_or_none()
        if token_entry is None:
            raise HTTPException(status_code=400, detail="Invalid transaction page token")
        statement = statement.where(or_(
            VirtualLedgerEntry.occurred_at < token_entry.occurred_at,
            and_(
                VirtualLedgerEntry.occurred_at == token_entry.occurred_at,
                VirtualLedgerEntry.id < token_entry.id,
            ),
        ))
    result = await db.execute(statement.order_by(VirtualLedgerEntry.occurred_at.desc(), VirtualLedgerEntry.id.desc()).limit(limit))
    entries = result.scalars().all()
    transactions = [{
        "id": str(entry.id),
        "activity_type": entry.entry_type.upper(),
        "symbol": entry.symbol,
        "side": entry.direction,
        "quantity": str(entry.quantity) if entry.quantity is not None else None,
        "amount": str(entry.amount if entry.direction == "credit" else -entry.amount),
        "occurred_at": entry.occurred_at.isoformat(),
        "description": entry.description or entry.entry_type.replace("_", " ").capitalize(),
    } for entry in entries]
    return {
        "account_id": virtual_account_public_id(account),
        "transactions": transactions,
        "count": len(transactions),
        "next_page_token": str(entries[-1].id) if len(entries) == limit and entries else None,
        "read_only": True,
    }


@app.get("/api/v1/trading/backoffice/reconciliation")
async def get_virtual_reconciliation_summary(
    _: uuid.UUID = Depends(get_current_backoffice_admin_id),
    db: AsyncSession = Depends(get_db),
):
    """Return partner-neutral, aggregate virtual-ledger controls for AFM operations."""
    accounts = (await db.execute(select(VirtualAccount).order_by(VirtualAccount.created_at.desc()).limit(100))).scalars().all()
    rows: list[dict] = []
    total_cash = Decimal("0")
    total_positions = 0
    pending = 0
    for account in accounts:
        cash = await get_virtual_cash_balance(account.id, db)
        positions = (await db.execute(select(VirtualPosition).where(VirtualPosition.virtual_account_id == account.id))).scalars().all()
        ledger_entries = (await db.execute(select(VirtualLedgerEntry.id).where(VirtualLedgerEntry.virtual_account_id == account.id))).scalars().all()
        last_reconciled = max((position.reconciled_at for position in positions if position.reconciled_at), default=None)
        reconciliation_status = "reconciled" if last_reconciled else "pending"
        total_cash += cash
        total_positions += len(positions)
        pending += int(reconciliation_status == "pending")
        rows.append({
            "account_id": virtual_account_public_id(account),
            "status": account.status,
            "currency": account.currency,
            "cash_balance": str(cash),
            "position_count": len(positions),
            "ledger_entry_count": len(ledger_entries),
            "reconciliation_status": reconciliation_status,
            "last_reconciled_at": last_reconciled.isoformat() if last_reconciled else None,
        })
    return {
        "accounts": rows,
        "summary": {
            "account_count": len(rows),
            "pending_reconciliation_count": pending,
            "position_count": total_positions,
            "aggregate_cash_balance": str(total_cash),
        },
        "read_only": True,
        "partner_details_exposed": False,
    }


class BrokerAccountLinkRequest(BaseModel):
    alpaca_account_id: str = Field(..., min_length=8, max_length=100)


async def get_linked_broker_account(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> BrokerAccountLink:
    result = await db.execute(
        select(BrokerAccountLink).where(
            BrokerAccountLink.user_id == user_id,
            BrokerAccountLink.status == "active",
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=409, detail="No active broker account is linked to this user")
    return link


@app.get("/api/v1/kora/balance")
async def get_kora_balance_readonly(
    _: uuid.UUID = Depends(get_current_backoffice_admin_id),
):
    """Return Kora payment-operations balances to an authorised AFM admin only."""
    try:
        return await get_kora_balance(settings)
    except KoraClientError as exc:
        raise HTTPException(status_code=502, detail="Payment balance is temporarily unavailable") from exc


@app.post("/api/v1/broker/account-link", status_code=status.HTTP_201_CREATED)
async def link_broker_account(
    payload: BrokerAccountLinkRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Verify and link one Alpaca account to the authenticated AFM user."""
    from market_gateway.alpaca_broker import alpaca_broker_client

    try:
        account = await alpaca_broker_client.get_account(payload.alpaca_account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to verify broker account: {exc}")

    result = await db.execute(select(BrokerAccountLink).where(BrokerAccountLink.user_id == user_id))
    link = result.scalar_one_or_none()
    if link is None:
        link = BrokerAccountLink(
            user_id=user_id,
            alpaca_account_id=payload.alpaca_account_id,
            status="active",
        )
        db.add(link)
    else:
        link.alpaca_account_id = payload.alpaca_account_id
        link.status = "active"
    await db.commit()
    return {"user_id": str(user_id), "account_id": account.get("id", payload.alpaca_account_id), "status": link.status}


@app.get("/api/v1/portfolio")
async def get_portfolio(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated user's linked Alpaca account and open positions."""
    from market_gateway.alpaca_broker import alpaca_broker_client

    link = await get_linked_broker_account(user_id, db)
    try:
        account, positions = await asyncio.gather(
            alpaca_broker_client.get_account_balance(link.alpaca_account_id),
            alpaca_broker_client.get_account_positions(link.alpaca_account_id),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to load broker portfolio: {exc}")
    return {
        "account_id": link.alpaca_account_id,
        "account": account,
        "positions": positions if isinstance(positions, list) else [],
    }


def normalize_broker_activity(activity: dict) -> dict:
    """Expose a stable, non-sensitive representation of one Alpaca account activity."""
    activity_type = str(activity.get("activity_type") or activity.get("type") or "OTHER")
    symbol = activity.get("symbol")
    side = activity.get("side")
    amount = activity.get("net_amount")

    if amount in (None, ""):
        try:
            amount_decimal = Decimal(str(activity.get("price"))) * Decimal(str(activity.get("qty")))
            if side == "buy":
                amount_decimal = -amount_decimal
            amount = f"{amount_decimal:.2f}"
        except (InvalidOperation, TypeError, ValueError):
            amount = None

    occurred_at = activity.get("transaction_time") or activity.get("created_at") or activity.get("date")
    description = activity.get("description")
    if not description:
        if activity_type == "FILL" and symbol:
            description = f"{str(side or 'trade').capitalize()} {symbol}"
        elif symbol:
            description = f"{activity_type} · {symbol}"
        else:
            description = activity_type

    return {
        "id": str(activity.get("id") or ""),
        "activity_type": activity_type,
        "symbol": symbol,
        "side": side,
        "quantity": activity.get("qty"),
        "amount": str(amount) if amount not in (None, "") else None,
        "occurred_at": occurred_at,
        "description": description,
    }


def build_activity_page(account_id: str, activities: list[dict], limit: int) -> dict:
    transactions = [normalize_broker_activity(activity) for activity in activities if isinstance(activity, dict)]
    next_page_token = transactions[-1]["id"] if len(transactions) == limit and transactions else None
    return {
        "account_id": account_id,
        "transactions": transactions,
        "count": len(transactions),
        "next_page_token": next_page_token,
    }


@app.get("/api/v1/portfolio/transactions")
async def get_portfolio_transactions(
    limit: int = Query(default=50, ge=1, le=100),
    page_token: str | None = Query(default=None, min_length=1, max_length=300),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return recent real Broker API activities for the authenticated linked account."""
    from market_gateway.alpaca_broker import alpaca_broker_client

    link = await get_linked_broker_account(user_id, db)
    try:
        params = {"direction": "desc", "page_size": limit}
        if page_token:
            params["page_token"] = page_token
        activities = await alpaca_broker_client.list_account_activities(link.alpaca_account_id, **params)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to load broker activities: {exc}")

    return build_activity_page(
        link.alpaca_account_id,
        activities if isinstance(activities, list) else [],
        limit,
    )


async def get_current_user(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = auth[7:]
    try:
        payload = decode_token(token)
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

async def rate_limit(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    await rate_limiter.is_allowed(
        f"ip:{client_ip}",
        limit=100,
        window_seconds=60,
    )
    return True

@app.get("/api/v1/wallet/balance", dependencies=[Depends(rate_limit)])
async def get_wallet_balance(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return broker account balances for the authenticated linked user."""
    from market_gateway.alpaca_broker import alpaca_broker_client

    link = await get_linked_broker_account(user_id, db)
    try:
        account = await alpaca_broker_client.get_account_balance(link.alpaca_account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to load broker balance: {exc}")
    return {"user_id": str(user_id), "account_id": link.alpaca_account_id, "balances": account}

@app.post("/dev/token")
async def issue_dev_token():
    """Issue a demo token only when the runtime environment is development."""
    if not get_settings().is_development:
        raise HTTPException(status_code=404, detail="Development token endpoint is unavailable")
    demo_user_id = str(uuid.uuid4())
    token = create_access_token({"sub": demo_user_id})
    return {"access_token": token, "token_type": "bearer", "user_id": demo_user_id}

class PaymentRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    method: str = Field(default="mobile_money")
    phone_number: str | None = None
    region: str = Field(default="west_africa")
    metadata: dict = Field(default_factory=dict)


class PayoutRequest(BaseModel):
    """Kora payout instruction; execution is opt-in and protected by confirmation."""
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    customer_email: str = Field(..., min_length=3)
    customer_name: str | None = None
    narration: str | None = None
    mobile_money_operator: str | None = None
    mobile_number: str | None = None
    bank_country: str | None = None
    bank_name: str | None = None
    bank_code: str | None = None
    account_number: str | None = None
    execute: bool = Field(default=False, description="Must be true to call Kora; false returns a validation-only preview.")


class RefundRequest(BaseModel):
    """Kora refund instruction; execution is opt-in and idempotent."""
    transaction_id: uuid.UUID
    amount: Decimal | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=200)
    execute: bool = Field(default=False, description="Must be true to call Kora; false returns a validation-only preview.")


class PaymentSimulationRequest(BaseModel):
    """Non-financial sandbox instruction used by the FrontierPay PSP console."""
    amount: Decimal = Field(..., ge=Decimal("100000"))
    source_currency: str = Field(..., min_length=3, max_length=3)
    beneficiary_currency: str = Field(..., min_length=3, max_length=3)
    corridor: str = Field(..., pattern="^(ci-ghana|ci-nigeria|benin-nigeria|cameroon-nigeria|cameroon-ivory-coast|ivory-coast-cameroon)$")
    kaybic_fee: Decimal = Field(default=Decimal("0"), ge=0)
    kora_payin_fee: Decimal = Field(default=Decimal("0"), ge=0)
    kora_payout_fee: Decimal = Field(default=Decimal("0"), ge=0)
    afm_fee: Decimal = Field(default=Decimal("0"), ge=0)
    fx_rate: Decimal = Field(default=Decimal("1"), gt=0, description="Used by authenticated sandbox intents; public simulation ignores caller-supplied rates.")
    direction: str = Field(default="payout", pattern="^(payout|payin)$")
    metadata: dict = Field(default_factory=dict)

KORA_DIRECT_FX_PAIRS = {
    ("XAF", "USD"), ("USD", "XAF"), ("XOF", "USD"), ("USD", "XOF"),
    ("USD", "NGN"), ("NGN", "USD"), ("USD", "GHS"), ("GHS", "USD"),
    ("USD", "ZAR"), ("ZAR", "USD"), ("USD", "KES"), ("KES", "USD"),
}


async def get_frontierpay_kora_quote(*, amount: Decimal, source_currency: str, beneficiary_currency: str, reference: str) -> dict:
    """Fetch a live Kora quote; chain through USD where Kora has no direct pair."""
    source_currency = source_currency.upper()
    beneficiary_currency = beneficiary_currency.upper()
    if source_currency == beneficiary_currency:
        return {"rate": Decimal("1"), "expiry_date": None, "expiry_in_seconds": None, "legs": []}
    client = KoraClient(get_settings())
    if (source_currency, beneficiary_currency) in KORA_DIRECT_FX_PAIRS:
        quote = await client.get_exchange_rate(
            amount=amount, from_currency=source_currency, to_currency=beneficiary_currency, reference=reference
        )
        return {"rate": quote["rate"], "expiry_date": quote["expiry_date"], "expiry_in_seconds": quote["expiry_in_seconds"], "legs": [quote]}
    if (source_currency, "USD") not in KORA_DIRECT_FX_PAIRS or ("USD", beneficiary_currency) not in KORA_DIRECT_FX_PAIRS:
        raise KoraClientError(f"Kora does not support the corridor {source_currency}/{beneficiary_currency}")
    first = await client.get_exchange_rate(
        amount=amount, from_currency=source_currency, to_currency="USD", reference=f"{reference}-1"
    )
    second = await client.get_exchange_rate(
        amount=first["to_amount"], from_currency="USD", to_currency=beneficiary_currency, reference=f"{reference}-2"
    )
    return {
        "rate": (first["rate"] * second["rate"]).quantize(Decimal("0.00000001")),
        "expiry_date": second["expiry_date"] or first["expiry_date"],
        "expiry_in_seconds": min(x for x in (first["expiry_in_seconds"], second["expiry_in_seconds"]) if isinstance(x, int)) if isinstance(first["expiry_in_seconds"], int) and isinstance(second["expiry_in_seconds"], int) else None,
        "legs": [first, second],
    }


class PublicPaymentSimulationResponse(BaseModel):
    """Public preview contract: consolidated fees only, with no financial side effect."""
    simulation_only: bool = True
    execution_mode: str
    amount: str
    source_currency: str
    beneficiary_currency: str
    corridor: str
    direction: str
    fx_rate: str
    rate_source: str = Field(default="live_corridor_quote", description="Server-side live corridor quote.")
    rate_expiry: str | None = None
    net_source_amount: str
    net_destination_amount: str
    platform_fees: str = Field(description="Total consolidated fees shown to the user.")
    funds_movement: bool = False
    ledger_write: bool = False


class PaymentResponse(BaseModel):
    transaction_id: str
    status: str
    amount: str
    currency: str
    fee_amount: str
    net_amount: str
    psp: str
    psp_transaction_id: str | None
    created_at: str


def serialize_transaction(transaction: Transaction) -> dict:
    metadata = transaction.txn_metadata or {}
    return {
        "transaction_id": str(transaction.id),
        "idempotency_key": transaction.idempotency_key,
        "status": transaction.status.value if hasattr(transaction.status, "value") else str(transaction.status),
        "amount": str(transaction.amount),
        "currency": transaction.currency,
        "fee_amount": str(transaction.fee_amount or 0),
        "total_fee_amount": str(transaction.total_fee_amount or transaction.fee_amount or 0),
        "fee_currency": transaction.fee_currency or transaction.currency,
        "net_amount": str(transaction.net_amount or 0),
        "psp": transaction.psp.value if hasattr(transaction.psp, "value") else str(transaction.psp),
        "psp_transaction_id": transaction.psp_transaction_id,
        "corridor": transaction.corridor,
        "beneficiary_currency": transaction.beneficiary_currency,
        "virtual_account_id": str(transaction.virtual_account_id) if transaction.virtual_account_id else None,
        "ledger_namespace": transaction.ledger_namespace,
        "segregation": {
            "funds_perimeter": metadata.get("funds_perimeter", transaction.ledger_namespace),
            "execution_mode": metadata.get("execution_mode", "unknown"),
            "balance_affecting": bool(metadata.get("balance_affecting", False)),
            "ledger_entry_count": metadata.get("ledger_entry_count", 0),
        },
        "fee_breakdown": metadata.get("fee_breakdown", {}),
        "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
        "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        "settled_at": transaction.settled_at.isoformat() if transaction.settled_at else None,
        "error_message": transaction.error_message,
    }


@app.post("/api/v1/public/frontierpay/simulate", status_code=status.HTTP_200_OK, response_model=PublicPaymentSimulationResponse, dependencies=[Depends(rate_limit)])
async def public_frontierpay_simulate(payment: PaymentSimulationRequest):
    """Public fee preview only: no authentication, database write or fund movement."""
    source_currency = payment.source_currency.upper()
    beneficiary_currency = payment.beneficiary_currency.upper()
    payin_fee = payment.kora_payin_fee
    payout_fee = payment.kora_payout_fee if payment.direction == "payout" else Decimal("0")
    afm_fee = payment.afm_fee
    client_psp_fee = payment.kaybic_fee
    total_fees = (payin_fee + payout_fee + afm_fee + client_psp_fee).quantize(Decimal("0.01"))
    net_source = max(Decimal("0"), payment.amount - total_fees).quantize(Decimal("0.01"))
    if net_source <= 0:
        raise HTTPException(status_code=422, detail="Total fees cannot exceed the source amount")
    try:
        quote = await get_frontierpay_kora_quote(
            amount=net_source,
            source_currency=source_currency,
            beneficiary_currency=beneficiary_currency,
            reference=f"frontierpay-public-{uuid.uuid4().hex}",
        )
    except KoraClientError as exc:
        raise HTTPException(status_code=503, detail="Live corridor rate is temporarily unavailable") from exc
    fx_rate = quote["rate"]
    net_destination = (net_source * fx_rate).quantize(Decimal("0.01"))
    return {
        "simulation_only": True,
        "execution_mode": "public_preview",
        "amount": str(payment.amount.quantize(Decimal("0.01"))),
        "source_currency": source_currency,
        "beneficiary_currency": beneficiary_currency,
        "corridor": payment.corridor,
        "direction": payment.direction,
        "fx_rate": str(fx_rate),
        "rate_source": "live_corridor_quote",
        "rate_expiry": quote.get("expiry_date"),
        "net_source_amount": str(net_source),
        "net_destination_amount": str(net_destination),
        "platform_fees": str(total_fees),
        "funds_movement": False,
        "ledger_write": False,
    }


@app.post("/api/v1/payments/simulate", status_code=status.HTTP_201_CREATED)
async def simulate_payment(
    payment: PaymentSimulationRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a non-financial, auditable sandbox payment intent.

    This route never calls a PSP, never moves money and never changes the cash balance.
    It only creates an AFM-scoped transaction record and a zero-value ledger journal entry.
    """
    if get_settings().is_production:
        raise HTTPException(status_code=404, detail="Simulation is unavailable in production")
    source_currency = payment.source_currency.upper()
    beneficiary_currency = payment.beneficiary_currency.upper()
    total_fees = (
        payment.kaybic_fee + payment.kora_payin_fee +
        (payment.kora_payout_fee if payment.direction == "payout" else Decimal("0")) +
        payment.afm_fee
    ).quantize(Decimal("0.01"))
    net_source = (payment.amount - total_fees).quantize(Decimal("0.01"))
    net_destination = (net_source * payment.fx_rate).quantize(Decimal("0.01"))
    if net_source < 0:
        raise HTTPException(status_code=422, detail="Total fees cannot exceed the source amount")

    account = await get_or_create_virtual_account(user_id, db)
    transaction = Transaction(
        idempotency_key=f"sandbox-{uuid.uuid4().hex}",
        user_id=user_id,
        psp=PSPType.KORA,
        amount=payment.amount,
        currency=source_currency,
        fee_amount=total_fees,
        fee_currency=source_currency,
        net_amount=net_source,
        status=PaymentStatus.PENDING,
        ledger_namespace="afm_payments",
        virtual_account_id=account.id,
        corridor=payment.corridor,
        beneficiary_currency=beneficiary_currency,
        total_fee_amount=total_fees,
        txn_metadata={
            **payment.metadata,
            "execution_mode": "sandbox_simulation",
            "funds_perimeter": "afm_virtual_ledger_only",
            "balance_affecting": False,
            "source_currency": source_currency,
            "beneficiary_currency": beneficiary_currency,
            "fx_rate": str(payment.fx_rate),
            "net_destination_amount": str(net_destination),
            "fee_breakdown": {
                "kaybic": str(payment.kaybic_fee.quantize(Decimal("0.01"))),
                "kora_payin": str(payment.kora_payin_fee.quantize(Decimal("0.01"))),
                "kora_payout": str((payment.kora_payout_fee if payment.direction == "payout" else Decimal("0")).quantize(Decimal("0.01"))),
                "afm": str(payment.afm_fee.quantize(Decimal("0.01"))),
                "total": str(total_fees),
            },
        },
    )
    db.add(transaction)
    await db.flush()
    journal = VirtualLedgerEntry(
        virtual_account_id=account.id,
        entry_type="payment_intent",
        direction="credit",
        amount=Decimal("0"),
        currency=source_currency,
        reference_type="transaction",
        reference_id=str(transaction.id),
        description="Sandbox payment intent — no funds movement",
        entry_metadata={"balance_affecting": False, "execution_mode": "sandbox_simulation"},
    )
    db.add(journal)
    transaction.txn_metadata = {**(transaction.txn_metadata or {}), "ledger_entry_count": 1, "ledger_reference_id": str(journal.id)}
    await db.commit()
    await db.refresh(transaction)
    return serialize_transaction(transaction) | {
        "net_destination_amount": str(net_destination),
        "simulation_only": True,
    }


@app.get("/api/v1/transactions")
async def list_dashboard_transactions(
    status_: str | None = Query(default=None, alias="status_"),
    psp: str | None = Query(default=None),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    search: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return only the authenticated merchant's transactions for dashboard access."""
    filters = [Transaction.user_id == user_id, Transaction.ledger_namespace == "afm_payments"]
    if status_:
        try:
            filters.append(Transaction.status == PaymentStatus(status_))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid transaction status")
    if psp:
        try:
            filters.append(Transaction.psp == PSPType(psp))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid PSP")
    if currency:
        filters.append(Transaction.currency == currency.upper())
    if search:
        needle = f"%{search}%"
        filters.append(or_(Transaction.id.cast(String).ilike(needle), Transaction.psp_transaction_id.ilike(needle)))
    total = (await db.execute(select(func.count()).select_from(Transaction).where(*filters))).scalar_one()
    rows = (await db.execute(
        select(Transaction).where(*filters)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {"items": [serialize_transaction(row) for row in rows], "pagination": {"page": page, "page_size": page_size, "total_count": total, "total_pages": total_pages}}


@app.get("/api/v1/transactions/{transaction_id}")
async def get_dashboard_transaction(
    transaction_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        parsed_id = uuid.UUID(transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid transaction id") from exc
    row = (await db.execute(select(Transaction).where(
        Transaction.id == parsed_id,
        Transaction.user_id == user_id,
        Transaction.ledger_namespace == "afm_payments",
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return serialize_transaction(row)


@app.post("/api/v1/payments", status_code=status.HTTP_201_CREATED)
async def create_payment(
    request: Request,
    payment: PaymentRequest,
    user_id: uuid.UUID = Depends(require_verified_user),
    db: AsyncSession = Depends(get_db),
):
    idempotency_key = request.headers.get("X-Idempotency-Key")
    await enforce_kyc1_monthly_limit(user_id=user_id, amount=payment.amount, currency=payment.currency, db=db)
    transaction = await payment_service.process_payment(
        user_id=user_id,
        amount=payment.amount,
        currency=payment.currency,
        method=payment.method,
        region=payment.region,
        phone_number=payment.phone_number,
        metadata=payment.metadata,
        idempotency_key=idempotency_key,
    )

    event = BaseEvent(
        event_type=EventType.PAYMENT_COMPLETED if transaction.status.value == "completed" else EventType.PAYMENT_FAILED,
        payload={
            "transaction_id": str(transaction.id),
            "user_id": str(transaction.user_id),
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "status": transaction.status.value,
            "psp": transaction.psp.value,
        },
    )
    await event_producer.publish(event)

    return {
        "transaction_id": str(transaction.id),
        "status": transaction.status.value,
        "amount": str(transaction.amount),
        "currency": transaction.currency,
        "fee_amount": str(transaction.fee_amount),
        "net_amount": str(transaction.net_amount),
        "psp": transaction.psp.value,
        "psp_transaction_id": transaction.psp_transaction_id,
        "created_at": transaction.created_at.isoformat(),
    }

@app.post("/api/v1/payouts/kora", status_code=status.HTTP_201_CREATED)
async def create_kora_payout(
    payout: PayoutRequest,
    request: Request,
    user_id: uuid.UUID = Depends(require_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Validate or execute a Kora payout.

    The default is validation-only. A live payout requires execute=true and an
    explicit confirmation header, preventing accidental fund movement from a
    retry, UI preview or automated test.
    """
    if bool(payout.mobile_money_operator) != bool(payout.mobile_number):
        raise HTTPException(status_code=422, detail="Mobile Money operator and number must be provided together")
    if not (payout.mobile_money_operator and payout.mobile_number) and not payout.account_number:
        raise HTTPException(status_code=422, detail="A Mobile Money destination or bank account is required")
    if payout.execute and request.headers.get("X-AFM-Payout-Confirmation") != "CONFIRM":
        raise HTTPException(status_code=428, detail="Explicit payout confirmation is required")
    if payout.execute:
        await enforce_kyc1_monthly_limit(user_id=user_id, amount=payout.amount, currency=payout.currency, db=db)
    reference = f"afm-payout-{uuid.uuid4().hex}"
    destination_type = "mobile_money" if payout.mobile_money_operator else "bank_account"
    account = await get_or_create_virtual_account(user_id, db)
    transaction = Transaction(
        idempotency_key=reference,
        user_id=user_id,
        psp=PSPType.KORA,
        amount=payout.amount,
        currency=payout.currency.upper(),
        net_amount=payout.amount,
        fee_currency=payout.currency.upper(),
        status=PaymentStatus.PROCESSING,
        virtual_account_id=account.id,
        mobile_money_phone=payout.mobile_number,
        ledger_namespace="afm_payments",
        txn_metadata={
            "operation": "payout",
            "destination_type": destination_type,
            "reconciliation_status": "pending",
            "balance_affecting": False,
        },
    )
    db.add(transaction)
    await db.flush()
    if not payout.execute:
        await db.commit()
        await db.refresh(transaction)
        return {
            "reference": reference,
            "transaction_id": str(transaction.id),
            "status": "validated",
            "execution_mode": "validation_only",
            "destination_type": destination_type,
            "amount": str(payout.amount.quantize(Decimal("0.01"))),
            "currency": payout.currency.upper(),
            "funds_movement": False,
            "ledger_write": False,
        }
    if get_settings().is_development:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Live payout is disabled in development")
    try:
        result = await KoraClient(get_settings()).create_payout(
            reference=reference,
            amount=payout.amount,
            currency=payout.currency,
            customer_email=payout.customer_email,
            customer_name=payout.customer_name,
            narration=payout.narration,
            mobile_money_operator=payout.mobile_money_operator,
            mobile_number=payout.mobile_number,
            bank_country=payout.bank_country,
            bank_name=payout.bank_name,
            bank_code=payout.bank_code,
            account_number=payout.account_number,
        )
    except KoraClientError as exc:
        transaction.status = PaymentStatus.FAILED
        transaction.error_message = "Kora payout is temporarily unavailable"
        transaction.txn_metadata = {**(transaction.txn_metadata or {}), "reconciliation_status": "exception"}
        await db.commit()
        raise HTTPException(status_code=502, detail="Kora payout is temporarily unavailable") from exc
    transaction.psp_transaction_id = str(result.get("reference") or result.get("transaction_reference") or reference)
    transaction.mobile_money_provider_reference = str(result.get("reference") or result.get("transaction_reference") or "") or None
    transaction.psp_response = result
    transaction.status = PaymentStatus.PROCESSING
    await db.commit()
    return {
        "reference": reference,
        "transaction_id": str(transaction.id),
        "status": str(result.get("status") or "processing"),
        "execution_mode": "kora_live",
        "destination_type": destination_type,
        "kora_response": result,
        "funds_movement": True,
        "ledger_write": False,
    }


@app.post("/api/v1/payments/{transaction_id}/refund", status_code=status.HTTP_201_CREATED)
async def create_kora_refund(
    transaction_id: uuid.UUID,
    refund: RefundRequest,
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Preview or initiate an idempotent refund for a settled AFM pay-in.

    The route never refunds a payout and never accepts a request without a
    payment reference created by AFM. A real Kora call requires both
    ``execute=true`` and ``X-AFM-Refund-Confirmation: CONFIRM``.
    """
    if refund.transaction_id != transaction_id:
        raise HTTPException(status_code=422, detail="Transaction id does not match the request path")

    transaction = await db.get(Transaction, transaction_id)
    if (
        transaction is None
        or transaction.user_id != user_id
        or transaction.psp != PSPType.KORA
        or transaction.ledger_namespace != "afm_payments"
    ):
        raise NotFoundError("Payment transaction not found")
    if str((transaction.txn_metadata or {}).get("operation") or "").lower() == "payout":
        raise HTTPException(status_code=409, detail="Payouts cannot be refunded through the pay-in refund route")
    if transaction.status not in {PaymentStatus.COMPLETED, PaymentStatus.REFUNDED}:
        raise HTTPException(status_code=409, detail="Only a completed pay-in can be refunded")
    if not transaction.psp_transaction_id:
        raise HTTPException(status_code=409, detail="Kora payment reference is missing")

    amount = (refund.amount or transaction.amount).quantize(Decimal("0.01"))
    if amount > transaction.amount:
        raise HTTPException(status_code=422, detail="Refund amount cannot exceed the payment amount")
    idempotency_key = request.headers.get("X-Idempotency-Key", "").strip()
    if refund.execute and len(idempotency_key) < 8:
        raise HTTPException(status_code=400, detail="X-Idempotency-Key is required for an executed refund")
    if refund.execute and request.headers.get("X-AFM-Refund-Confirmation") != "CONFIRM":
        raise HTTPException(status_code=428, detail="Explicit refund confirmation is required")

    if not refund.execute:
        return {
            "status": "validated",
            "execution_mode": "validation_only",
            "transaction_id": str(transaction.id),
            "payment_reference": transaction.psp_transaction_id,
            "amount": str(amount),
            "currency": transaction.currency,
            "funds_movement": False,
            "ledger_write": False,
        }
    if get_settings().is_development:
        raise HTTPException(status_code=409, detail="Executed refunds are disabled in development")

    refund_reference = f"afm-ref-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:40]}"
    existing_result = await db.execute(
        select(KoraRefund).where(KoraRefund.idempotency_key == idempotency_key)
    )
    existing_refund = existing_result.scalar_one_or_none()
    if existing_refund is not None:
        return {
            "status": existing_refund.status,
            "refund_reference": existing_refund.refund_reference,
            "payment_reference": existing_refund.payment_reference,
            "amount": str(existing_refund.amount),
            "currency": existing_refund.currency,
            "idempotent_replay": True,
            "funds_movement": existing_refund.status in {"requested", "processing", "success"},
        }

    refund_record = KoraRefund(
        transaction_id=transaction.id,
        refund_reference=refund_reference,
        idempotency_key=idempotency_key,
        payment_reference=transaction.psp_transaction_id,
        amount=amount,
        currency=transaction.currency,
        reason=refund.reason,
        status="requested",
        psp_response={},
    )
    db.add(refund_record)
    await db.commit()
    try:
        result = await KoraClient(get_settings()).initiate_refund(
            payment_reference=transaction.psp_transaction_id,
            refund_reference=refund_reference,
            amount=amount,
            reason=refund.reason,
            webhook_url="https://africafrontiermarkets.com/webhooks/kora",
        )
        refund_record.status = str(result.get("status") or "processing").lower()
        refund_record.psp_response = result
        transaction.txn_metadata = {
            **(transaction.txn_metadata or {}),
            "refund_status": refund_record.status,
            "refund_reference": refund_reference,
        }
        await db.commit()
    except KoraClientError as exc:
        refund_record.status = "failed"
        refund_record.error_message = "Kora refund is temporarily unavailable"
        await db.commit()
        raise HTTPException(status_code=502, detail="Kora refund is temporarily unavailable") from exc

    return {
        "status": refund_record.status,
        "refund_reference": refund_reference,
        "payment_reference": transaction.psp_transaction_id,
        "amount": str(amount),
        "currency": transaction.currency,
        "idempotent_replay": False,
        "funds_movement": True,
        "ledger_write": False,
    }


@app.get("/api/v1/payments/{transaction_id}")
async def get_payment(
    transaction_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    try:
        parsed_id = uuid.UUID(transaction_id)
    except ValueError:
        raise ValidationError(f"'{transaction_id}' is not a valid transaction id")

    transaction = await payment_service.get_transaction(parsed_id)

    if transaction.user_id != user_id:
        raise NotFoundError(f"Transaction {transaction_id} not found")

    return {
        "transaction_id": str(transaction.id),
        "status": transaction.status.value,
        "amount": str(transaction.amount),
        "currency": transaction.currency,
        "psp": transaction.psp.value,
        "psp_transaction_id": transaction.psp_transaction_id,
        "created_at": transaction.created_at.isoformat(),
    }

async def _process_kora_refund_webhook(data: dict, db: AsyncSession) -> None:
    """Reconcile a refund callback and create one reversal entry at most."""
    refund_reference = str(data.get("reference") or "")
    payment_reference = str(data.get("payment_reference") or "")
    if not refund_reference or not payment_reference:
        raise ValueError("Kora refund webhook is missing refund or payment reference")

    refund_result = await db.execute(
        select(KoraRefund).where(
            or_(
                KoraRefund.refund_reference == refund_reference,
                KoraRefund.payment_reference == payment_reference,
            )
        ).order_by(KoraRefund.created_at.desc())
    )
    refund = refund_result.scalars().first()
    if refund is None:
        raise ValueError("Kora refund webhook reference does not match an AFM refund")

    transaction = await db.get(Transaction, refund.transaction_id)
    if transaction is None or transaction.ledger_namespace != "afm_payments":
        raise ValueError("Kora refund parent transaction is outside AFM payment ledger")

    status_value = str(data.get("status") or "").lower()
    refund.psp_response = {**(refund.psp_response or {}), "webhook": data}
    if status_value in {"success", "successful", "completed", "settled"}:
        refund.status = "success"
        refund.completed_at = datetime.now(timezone.utc)
        transaction.status = PaymentStatus.REFUNDED
        transaction.txn_metadata = {
            **(transaction.txn_metadata or {}),
            "refund_status": "success",
            "refund_reference": refund.refund_reference,
            "reconciliation_status": "matched",
        }
        if transaction.virtual_account_id:
            existing_entry = (
                await db.execute(
                    select(VirtualLedgerEntry).where(
                        VirtualLedgerEntry.reference_type == "kora_refund",
                        VirtualLedgerEntry.reference_id == refund.refund_reference,
                    )
                )
            ).scalar_one_or_none()
            if existing_entry is None:
                db.add(
                    VirtualLedgerEntry(
                        virtual_account_id=transaction.virtual_account_id,
                        entry_type="refund",
                        direction="debit",
                        amount=refund.amount,
                        currency=refund.currency,
                        reference_type="kora_refund",
                        reference_id=refund.refund_reference,
                        description="Kora pay-in refund reconciled",
                        entry_metadata={
                            "transaction_id": str(transaction.id),
                            "payment_reference": refund.payment_reference,
                            "reconciliation_status": "matched",
                            "balance_affecting": True,
                        },
                    )
                )
    elif status_value in {"failed", "cancelled", "canceled", "reversed"}:
        refund.status = "failed"
        refund.error_message = str(data.get("status_reason") or data.get("message") or "Kora refund failed")[:255]
        transaction.txn_metadata = {
            **(transaction.txn_metadata or {}),
            "refund_status": "failed",
            "refund_reference": refund.refund_reference,
            "reconciliation_status": "exception",
        }
    else:
        refund.status = "processing"
        transaction.txn_metadata = {
            **(transaction.txn_metadata or {}),
            "refund_status": "processing",
            "refund_reference": refund.refund_reference,
        }


async def process_kora_webhook_business_event(
    body: dict, event_type: str, db: AsyncSession
) -> None:
    """Apply a verified Kora event to the AFM transaction and ledger.

    Webhooks never initiate money movement. They only reconcile an already
    created transaction. Every payout or refund ledger entry is idempotent.
    """
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    event_name = event_type.lower()
    if event_name.startswith("refund"):
        await _process_kora_refund_webhook(data, db)
        return

    reference = str(
        data.get("reference")
        or data.get("transaction_reference")
        or data.get("payment_reference")
        or ""
    )
    if not reference:
        raise ValueError("Kora webhook is missing a transaction reference")

    is_payout = event_name.startswith(("transfer", "payout", "disbursement"))
    transaction_result = await db.execute(
        select(Transaction).where(
            or_(
                Transaction.idempotency_key == reference,
                Transaction.psp_transaction_id == reference,
                Transaction.psp_payment_reference == reference,
            ),
            Transaction.ledger_namespace == "afm_payments",
        )
    )
    transaction = transaction_result.scalar_one_or_none()
    if transaction is None or not hasattr(transaction, "user_id"):
        if is_payout:
            raise ValueError("Kora payout webhook reference does not match an AFM transaction")
        return

    status_value = str(data.get("status") or "").lower()
    transaction.webhook_received_at = datetime.now(timezone.utc)
    transaction.psp_response = {**(transaction.psp_response or {}), "webhook": data}
    if status_value in {"success", "successful", "completed", "settled"}:
        transaction.status = PaymentStatus.COMPLETED
        transaction.settled_at = datetime.now(timezone.utc)
        if is_payout and transaction.virtual_account_id:
            existing_entry = (
                await db.execute(
                    select(VirtualLedgerEntry).where(
                        VirtualLedgerEntry.reference_type == "kora_payout",
                        VirtualLedgerEntry.reference_id == reference,
                    )
                )
            ).scalar_one_or_none()
            if existing_entry is None:
                db.add(
                    VirtualLedgerEntry(
                        virtual_account_id=transaction.virtual_account_id,
                        entry_type="payout",
                        direction="debit",
                        amount=transaction.amount,
                        currency=transaction.currency,
                        reference_type="kora_payout",
                        reference_id=reference,
                        description="Kora payout reconciled",
                        entry_metadata={
                            "transaction_id": str(transaction.id),
                            "reconciliation_status": "matched",
                            "balance_affecting": True,
                        },
                    )
                )
                transaction.txn_metadata = {
                    **(transaction.txn_metadata or {}),
                    "reconciliation_status": "matched",
                    "ledger_entry_count": int((transaction.txn_metadata or {}).get("ledger_entry_count", 0)) + 1,
                }
    elif status_value in {"failed", "cancelled", "canceled", "reversed"}:
        transaction.status = PaymentStatus.FAILED
        transaction.error_message = str(data.get("message") or f"Kora payout status: {status_value}")[:255]
        transaction.txn_metadata = {
            **(transaction.txn_metadata or {}),
            "reconciliation_status": "exception",
        }
    else:
        transaction.status = PaymentStatus.PROCESSING
        await enqueue_reconciliation(db, transaction, data)
        transaction.txn_metadata = {
            **(transaction.txn_metadata or {}),
            "reconciliation_status": "scheduled",
            "provider_status": status_value,
        }


@app.post("/webhooks/kora")
async def kora_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("X-Korapay-Signature") or request.headers.get("X-Kora-Signature", "")
    try:
        body = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Malformed webhook payload") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be an object")

    signed_data = body.get("data", body)
    if not isinstance(signed_data, dict):
        raise HTTPException(status_code=400, detail="Webhook data must be an object")

    settings = get_settings()
    webhook_secret = settings.kora_webhook_secret or settings.kora_secret_key
    if not webhook_secret or not verify_kora_webhook_signature(signed_data, signature, webhook_secret):
        # Kora recommends HTTP 200 for webhook acknowledgement. Invalid
        # signatures are ignored without persistence or business processing;
        # returning 200 prevents pointless provider retries while preserving
        # the security boundary.
        logger.warning("Kora webhook ignored: invalid signature")
        return {"status": "ignored", "reason": "invalid_signature"}

    payload_hash = hashlib.sha256(payload).hexdigest()
    event_type = str(body.get("event") or body.get("type") or "unknown")[:80]
    provider_event_id = body.get("id") or signed_data.get("id")
    provider_reference = (
        signed_data.get("reference")
        or signed_data.get("transaction_reference")
        or signed_data.get("payment_reference")
    )
    event_id = str(
        provider_event_id
        or f"{event_type}:{provider_reference or 'unknown'}:{payload_hash}"
    )[:128]

    event = KoraWebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payload_hash=payload_hash,
        status="received",
        payload=body,
    )
    try:
        db.add(event)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.execute(select(KoraWebhookEvent).where(KoraWebhookEvent.event_id == event_id))
        existing_event = existing.scalar_one_or_none()
        if existing_event is not None:
            return {"status": f"already_{existing_event.status}", "event_id": event_id}
        raise HTTPException(status_code=503, detail="Webhook idempotency store unavailable")

    try:
        await process_kora_webhook_business_event(body, event_type, db)
        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info("Kora webhook processed", event_type=event_type, event_id=event_id)
        return {"status": "processed", "event_id": event_id}
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        event.status = "failed"
        event.error_message = str(exc)[:255]
        await db.commit()
        failures = await db.scalar(
            select(func.count(KoraWebhookEvent.id)).where(
                KoraWebhookEvent.event_type == event_type,
                KoraWebhookEvent.status == "failed",
                KoraWebhookEvent.received_at >= datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        await notify_kora_failure(
            event_id=event_id,
            event_type=event_type,
            failure_count=int(failures or 0),
            error=str(exc),
        )
        support_decision = await enrich_with_external_llm(
            analyze_incident(
                str(exc),
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "provider_reference": provider_reference,
                    "failure_count": int(failures or 0),
                },
            )
        )
        logger.error(
            "Corrective support diagnosis",
            incident_key=support_decision.incident_key,
            category=support_decision.category,
            severity=support_decision.severity,
            proposed_action=support_decision.proposed_action,
            auto_action_allowed=support_decision.auto_action_allowed,
            requires_human_approval=support_decision.requires_human_approval,
        )
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

@app.post("/webhooks/fincra")
async def fincra_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("X-Fincra-Signature", "")
    if not await payment_service.verify_webhook("fincra", payload, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    data = await request.json()
    logger.info("Fincra webhook received", event=data.get("event"))
    return {"status": "received"}

@app.post("/platforms")
async def onboard_platform(request: Request):
    from platform_manager.platform_service import platform_service
    data = await request.json()
    result = await platform_service.onboard_platform(
        name=data["name"],
        contact_email=data["contact_email"],
        webhook_url=data.get("webhook_url"),
    )
    return result

@app.post("/platforms/{platform_id}/rotate-key")
async def rotate_api_key(platform_id: str, user=Depends(get_current_user)):
    from platform_manager.platform_service import platform_service
    result = await platform_service.rotate_key(platform_id)
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_gateway.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.is_development,
        access_log=False,
    )
