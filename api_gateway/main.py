"""
AFM API Gateway — FastAPI with real payment endpoint
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator

from pydantic import BaseModel, Field
from fastapi import FastAPI, Request, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.config import get_settings
from config.database import init_db, engine, get_db
from config.exceptions import (
    AFMException, ValidationError, NotFoundError,
)
from config.logging_config import configure_logging
from config.rate_limit import rate_limiter
from config.security import decode_token, get_current_user_id, create_access_token
from config.telemetry import app_info, http_requests_total, http_request_duration, get_metrics_response, CONTENT_TYPE_LATEST
from event_bus.redis_producer import event_producer
from event_bus.event_schema import BaseEvent, EventType
from payment_hub.payment_service import payment_service
from payment_hub.models import BrokerAccountLink
from api_gateway.auth import router as auth_router

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
    yield
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

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Idempotency-Key"],
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


@app.get("/markets")
@app.get("/compliance")
async def public_web_pages():
    """Serve the standalone public web experience without involving the mobile app."""
    index_path = PUBLIC_DIR / "afm-web.html"
    if index_path.exists():
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Public web frontend not found")


@app.get("/onboarding")
async def public_onboarding_page():
    """Provide a stable public onboarding link that opens the Compliance section."""
    return RedirectResponse(url="/#compliance", status_code=status.HTTP_302_FOUND)


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


async def get_tradable_broker_assets(asset_class: str | None = None) -> list[dict]:
    """Fetch and cache active, tradable Broker API assets for a short interval."""
    from market_gateway.alpaca_broker import alpaca_broker_client

    cache_key = asset_class or "all"
    cached = _instrument_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _INSTRUMENT_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        params = {"status": "active"}
        if asset_class:
            params["asset_class"] = asset_class
        assets = await alpaca_broker_client.list_assets(**params)
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
    """Fetch cached daily Alpaca bars once for a bounded equity symbol batch."""
    clean_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})[:25]
    if not clean_symbols:
        return {}
    cache_key = f"{'|'.join(clean_symbols)}:{days}:{timeframe}"
    cached = _market_history_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _MARKET_HISTORY_CACHE_TTL_SECONDS:
        return cached[1]
    from market_gateway.alpaca_broker import alpaca_broker_client

    start = (datetime.now(timezone.utc) - timedelta(days=days + 4)).isoformat()
    stock_symbols = [symbol for symbol in clean_symbols if "/" not in symbol]
    crypto_symbols = [symbol for symbol in clean_symbols if "/" in symbol]
    limit = min(1000, len(clean_symbols) * (days * (8 if timeframe == "1Hour" else 1) + 8))
    requests = []
    if stock_symbols:
        requests.append(alpaca_broker_client.get_stock_bars(
            stock_symbols,
            timeframe=timeframe,
            start=start,
            adjustment="all",
            feed="iex",
            limit=limit,
        ))
    if crypto_symbols:
        requests.append(alpaca_broker_client.get_crypto_bars(
            crypto_symbols,
            timeframe=timeframe,
            start=start,
            limit=limit,
        ))
    try:
        payloads = await asyncio.gather(*requests)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Unable to load market data: {exc}")
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


@app.get("/api/v1/broker/assets")
async def broker_list_assets(
    asset_class: str | None = Query(default=None, max_length=50),
    query: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """List active, tradable Alpaca instruments through a filtered, paginated response."""
    tradable_assets = await get_tradable_broker_assets(asset_class)
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


@app.get("/api/v1/broker/instruments")
async def broker_list_instruments(
    asset_class: str | None = Query(default=None, max_length=50),
    query: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """Frontend-friendly alias for the validated tradable instrument list."""
    return await broker_list_assets(
        asset_class=asset_class,
        query=query,
        page=page,
        page_size=page_size,
    )


@app.get("/api/v1/public/market-products")
async def public_market_products():
    """Return five verified instruments, including supported crypto pairs, without order actions."""
    tradable_assets = await get_tradable_broker_assets()
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


@app.get("/api/v1/broker/market-snapshots")
async def broker_market_snapshots(
    symbols: str = Query(..., min_length=1, max_length=500),
):
    """Return bounded, cache-backed Alpaca price snapshots for catalogue sparklines."""
    requested = [symbol for symbol in symbols.split(",") if symbol.strip()]
    histories = await get_market_histories(requested, days=7)
    return {"snapshots": histories}


@app.get("/api/v1/broker/instruments/{symbol}/history")
async def broker_instrument_history(
    symbol: str,
    period: str = Query(default="1M", pattern="^(1D|1W|1M|1Y)$"),
):
    """Return historical Alpaca daily bars for one market instrument. No order is created."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol.replace("-", "").replace(".", "").isalnum():
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

if get_settings().is_development:
    @app.post("/dev/token")
    async def issue_dev_token():
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

@app.post("/api/v1/payments", status_code=status.HTTP_201_CREATED)
async def create_payment(
    request: Request,
    payment: PaymentRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    idempotency_key = request.headers.get("X-Idempotency-Key")
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

@app.post("/webhooks/kora")
async def kora_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("X-Kora-Signature", "")
    if not await payment_service.verify_webhook("kora", payload, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    data = await request.json()
    logger.info("Kora webhook received", event=data.get("event"))
    return {"status": "received"}

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
