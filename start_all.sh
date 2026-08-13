#!/bin/bash

# Transformer DATABASE_URL pour correspondre au format attendu par l'application
export DATABASE_URL=$(echo $DATABASE_URL | sed 's/postgresql:\/\//postgresql+asyncpg:\/\//')

# Lancer les migrations de base de données
echo "Running migrations..."
alembic upgrade head

# Lancer le Worker en arrière-plan (sera séparé dans une optimisation future)
echo "Starting background worker..."
python -m event_bus.worker &

# Lancer l'API Gateway au premier plan
echo "Starting API Gateway..."
uvicorn api_gateway.main:app --host 0.0.0.0 --port $PORT
