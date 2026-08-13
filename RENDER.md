# Deploy on Render.com

## Option 1: Blueprint (recommandé)

1. Fork/push ce repo sur GitHub
2. Dans Render Dashboard → **Blueprints** → **New Blueprint Instance**
3. Connecter le repo GitHub
4. Render détecte automatiquement `render.yaml`
5. Les services se créent : API + Worker + DB + Redis + Static Site

## Option 2: Manuel (service par service)

### Étape 1: PostgreSQL
- Dashboard → **New PostgreSQL**
- Name: `afm-postgres`
- Plan: Standard
- Region: Frankfurt
- Copier la **Internal Connection String**

### Étape 2: Redis
- Dashboard → **New Redis**
- Name: `afm-redis`
- Plan: Starter
- Region: Frankfurt
- Copier la **Internal Connection String**

### Étape 3: Web Service (API)
- Dashboard → **New Web Service**
- Connecter le repo
- Runtime: Python 3
- Build Command: `pip install -r requirements.txt && alembic upgrade head`
- Start Command: `uvicorn api_gateway.main:app --host 0.0.0.0 --port $PORT --workers 4`
- Health Check Path: `/health`
- Variables d'env:
  - `DATABASE_URL` = Internal DB URL
  - `REDIS_URL` = Internal Redis URL
  - `SECRET_KEY` = `openssl rand -hex 32`
  - `KORA_API_KEY`, `KORA_SECRET_KEY`, etc.

### Étape 4: Worker
- Dashboard → **New Background Worker**
- Start Command: `python -m event_bus.worker`
- Mêmes variables d'env que l'API

### Étape 5: Static Site (Frontend)
- Dashboard → **New Static Site**
- Build Command: `echo "no build"`
- Publish Directory: `frontend/`

## URLs après déploiement

| Service | URL |
|---------|-----|
| API | `https://afm-api-gateway.onrender.com` |
| Health | `https://afm-api-gateway.onrender.com/health` |
| Ready | `https://afm-api-gateway.onrender.com/ready` |
| Metrics | `https://afm-api-gateway.onrender.com/metrics` |
| Frontend | `https://afm-frontend.onrender.com` |

## Webhooks PSP

Configurer dans les dashboards Kora/Fincra:
- Kora webhook URL: `https://afm-api-gateway.onrender.com/webhooks/kora`
- Fincra webhook URL: `https://afm-api-gateway.onrender.com/webhooks/fincra`

## Migrations

Render exécute `alembic upgrade head` au build. Pour rollback:
```bash
render ssh --service afm-api-gateway
alembic downgrade -1
```

## Logs

```bash
render logs --service afm-api-gateway --tail
render logs --service afm-worker --tail
```
