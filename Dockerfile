# AFM — Dockerfile for Northflank deployment.
#
# Builds the Africa Frontier Markets API gateway as a container.
# The service listens on $PORT (default 8000, matching the Northflank
# service port configuration). Database migrations run from entrypoint.sh
# when AFM_RUN_MIGRATIONS=true.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential: needed for source builds if a wheel is ever missing for
# the target platform (asyncpg/bcrypt/cryptography normally ship wheels,
# but this keeps the image resilient to that not being true on some arch).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd --create-home afm
USER afm

ENTRYPOINT ["/entrypoint.sh"]

EXPOSE 8000

# Default: run the API gateway, listening on $PORT (Northflank injects PORT;
# falls back to 8000 to match the service's configured port).
CMD ["sh", "-c", "uvicorn api_gateway.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
