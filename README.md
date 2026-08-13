# Africa Frontier Markets — Production Ready

## Architecture
- **EasyMarkets**: Trading platform with copy trading, social signals, wallet
- **FrontierPay**: Cross-border payments via Kora, Fincra, Flutterwave, Stripe
- **Revenue Engine**: 35% Ads / 50% Creator / 15% Platform split

## Quick Start
```bash
# 1. Configure
cp .env.example .env
# Edit: SECRET_KEY (>=48 chars), DB_PASSWORD, PSP API keys (LIVE)

# 2. Staging (Docker Compose)
docker-compose -f infra/docker-compose.prod.yml up -d

# 3. Verify
curl http://localhost:8000/health
curl http://localhost:8000/ready

# 4. Tests
pytest tests/ -v
```

## Production (Kubernetes)
```bash
kubectl apply -f infra/k8s/namespace.yml
kubectl apply -f infra/k8s/configmap.yml
# Edit infra/k8s/secrets.yml with real secrets
kubectl apply -f infra/k8s/secrets.yml
kubectl apply -f infra/k8s/postgres-deployment.yml
kubectl apply -f infra/k8s/redis-deployment.yml
kubectl apply -f infra/k8s/api-gateway-deployment.yml
kubectl apply -f infra/k8s/ingress.yml
kubectl apply -f infra/k8s/hpa.yml
```

## V45 Corrections Applied
1. ZoneInfo + weekend closed (market_registry.py)
2. SHA256 stable lock ID (payment_service.py)
3. SUPPORTED_CURRENCIES + amount>0 (wallet_service.py)
4. SelfFollowError + allocation>0 (copy_trading_service.py)
5. timedelta imported (platform_service.py)
6. TransactionType enum (main.py)
7. FX_TO_USD centralized (dashboard_service.py)
# Africa Frontier Markets (AFM) — Production Build

## ⚠️ Scope reality check

The API description and settings (`alpaca_api_key`, `max_order_value_usd`,
`max_user_exposure_usd`, `fx_spread_bps`, etc.) advertise a **payments +
US-equity-trading** platform. Only the **payments** side has any
implementation. `trading_engine/` and `market_gateway/` are empty packages
(`__init__.py` only) — no Alpaca integration, no order routing, no exposure
limit enforcement exists yet, even though the config models it. Treat
trading as an unbuilt Phase 2, not a working feature, until that code
actually lands.

## Bugs fixed (code review pass)

| Issue | Why it mattered | Fix |
|-------|------------------|-----|
| `user_id` hardcoded to `"user_demo_001"` | Not a valid UUID, no matching row in `users` → every real payment call crashed against Postgres (invalid UUID / FK violation), and the endpoint had **no actual authentication** despite JWT code existing unused | Real JWT bearer auth (`config.security.get_current_user_id`) wired into `create_payment` / `get_payment` |
| No way to obtain a token in production | `/dev/token` is dev-only by design, so once deployed with `ENVIRONMENT=production` the API was **completely unusable** — no login system existed | Added `api_gateway/auth.py`: real `/api/v1/auth/register`, `/login`, `/refresh` backed by the existing `users` table |
| Idempotency key was non-deterministic without an `X-Idempotency-Key` header | A network retry of an identical request got a brand-new key every time → **duplicate charges** | Deterministic fallback key derived from `user_id + amount + currency + method + phone + time bucket`; client-supplied keys now scoped per-user |
| MTN MoMo / Orange Money credential check didn't match actual routing | Could **silently simulate a successful payment in production** if only Flutterwave creds were set (see `payment_service._call_psp_api`) | Routing now follows whichever aggregator actually has a secret key; every PSP call now hard-fails in production instead of faking success if credentials are missing |
| `revenue_engine.calculate_fee` applied raw USD ($0.50/$50) bounds to non-USD amounts | Diverged from what `payment_service` actually charges on XOF/NGN/etc | Both now share one FX-aware bound converter (`common/fx.py`) |
| `get_payment` accepted any string as `transaction_id`, no ownership check | Invalid IDs caused raw unhandled DB errors; any user could read any other user's transaction | UUID validated up front; transaction scoped to the authenticated caller |
| No schema migrations despite `alembic` in requirements.txt | `init_db()` used `create_all()` on every boot — no migration history, unsafe for prod schema changes | Added `alembic/` with an initial migration mirroring `payment_hub/models.py`; production applies it via `alembic upgrade head` (Render `preDeployCommand`), dev still auto-creates for convenience |
| `DATABASE_URL` assumed `postgresql+asyncpg://` | Render (and most platforms) inject `postgresql://`, sometimes with `?sslmode=require`, which asyncpg's SQLAlchemy dialect can't parse directly | `common/db_url.py` normalizes the scheme and translates `sslmode` into an asyncpg `ssl` connect arg |
| README claimed a worker with "real event handling" that didn't exist | No worker file existed anywhere in the repo | Added `event_bus/worker.py`, a runnable consumer with per-event-type handling |

## Architecture

```
Client → API Gateway (JWT auth) → Payment Service → PSP Router → REAL PSP API
                                       ↓
                                 PostgreSQL (transactions, users)
                                       ↓
                                 Redis Streams (events) → event_bus/worker.py
```

## Local development

```bash
pip install -r requirements-dev.txt   # includes requirements.txt + pytest
export $(cat .env.example | xargs)
uvicorn api_gateway.main:app --reload
```

In dev, tables are auto-created (`init_db()`); in staging/production, use
Alembic (see below).

### Try it locally

```bash
# 1. Register (or use /dev/token, available only when ENVIRONMENT=development)
curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"merchant@example.com","password":"a-strong-password"}' \
  | tee /tmp/afm_auth.json

TOKEN=$(python3 -c "import json;print(json.load(open('/tmp/afm_auth.json'))['access_token'])")

# 2. Create a payment
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": "10000",
    "currency": "XOF",
    "method": "mobile_money",
    "phone_number": "+22501234567",
    "region": "west_africa"
  }'
```

---

## Deploying to Render

This repo ships a [`render.yaml`](./render.yaml) Blueprint that provisions
everything in one pass: the API, a background worker, managed Postgres,
and managed Redis (Key Value).

### 1. Push to Git

Render Blueprints deploy from a Git repo (GitHub/GitLab/Bitbucket).

```bash
git init
git add .
git commit -m "AFM production build"
git remote add origin <your-repo-url>
git push -u origin main
```

### 2. Deploy the Blueprint

In the Render Dashboard: **New → Blueprint**, select your repo. Render
reads `render.yaml` and shows you everything it's about to create:

- `afm-db` — managed PostgreSQL
- `afm-redis` — managed Key Value (Redis-compatible), used for event
  streams
- `afm-api` — the FastAPI web service (runs `alembic upgrade head` as a
  pre-deploy step, then `uvicorn` with `--workers`)
- `afm-worker` — background worker running `event_bus/worker.py`

You'll be prompted for every env var marked `sync: false` in
`render.yaml`:

- `ALLOWED_ORIGINS` — your real frontend domain(s), comma-separated
- PSP credentials (`KORA_*`, `FINCRA_*`, `FLUTTERWAVE_*`, `STRIPE_*`) —
  leave blank for any PSP you're not using yet; `psp_router` skips PSPs
  with no credentials, and the app now refuses to fake a payment result
  in production if none are configured (see bug-fix table above)

`SECRET_KEY` is auto-generated by Render (`generateValue: true`) — you
never see or set it manually.

Click **Deploy Blueprint**. First deploy provisions the DB/Redis, builds
the API and worker, runs the initial migration, and goes live on your
`*.onrender.com` URL (or a custom domain you attach afterward).

### 3. Verify

```bash
curl https://afm-api.onrender.com/health

curl -X POST https://afm-api.onrender.com/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-strong-password"}'
```

### Plan/region notes

- Defaults to the paid **starter** plan and **frankfurt** region in
  `render.yaml` — edit both to taste. `preDeployCommand` (used to run
  migrations) requires a paid instance type; on the free tier, move
  `alembic upgrade head` into `buildCommand` instead.
- Connection pool sizes (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`,
  `REDIS_POOL_SIZE`) are set conservatively for starter-tier
  Postgres/Redis. Total connections ≈ pool size × `WEB_CONCURRENCY` ×
  number of running instances — check this against your plan's
  connection limit before scaling out.

### Schema changes after the first deploy

```bash
# locally, against a Postgres you can reach:
alembic revision --autogenerate -m "describe your change"
git add alembic/versions/*.py
git commit -m "migration: describe your change"
git push
```

Render's `preDeployCommand` runs `alembic upgrade head` automatically on
every subsequent deploy.

### Optional: Docker instead of the native Python runtime

A [`Dockerfile`](./Dockerfile) + [`entrypoint.sh`](./entrypoint.sh) are
included as an alternative path if you'd rather deploy a container.
Switch the `afm-api` / `afm-worker` blocks in `render.yaml` to
`runtime: docker`, and set `AFM_RUN_MIGRATIONS=true` on the API service
only (the entrypoint runs migrations before exec'ing the real command —
see comments in both files for why this doesn't rely on
`preDeployCommand`).

---

## Known remaining gaps (flagged, not hidden)

- `trading_engine/` and `market_gateway/` are unimplemented (see scope
  note above).
- No password-reset / email-verification flow — `api_gateway/auth.py`
  covers register/login/refresh only.
- No automated tests exist (`tests/` is empty) despite `pytest` now in
  `requirements-dev.txt`.
- `_detect_provider` (mobile-money network detection from phone prefix)
  is a coarse heuristic, not a real carrier lookup — e.g. it defaults
  every Nigerian number to `"mtn"` regardless of actual network.
- FX rates in `common/fx.py` are hardcoded/illustrative — wire in a real
  FX rate feed before relying on them for anything beyond fee-bound
  estimates.
