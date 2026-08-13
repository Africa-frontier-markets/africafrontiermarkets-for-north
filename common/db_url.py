"""
AFM Deployment URL Helpers.

Render (and Heroku-style platforms) inject `DATABASE_URL` as
`postgresql://user:pass@host:port/db` — sometimes with a `?sslmode=require`
query param. SQLAlchemy's asyncpg dialect needs the scheme
`postgresql+asyncpg://` and does NOT understand `sslmode` as a query
param (asyncpg wants `ssl=...` passed as a driver connect arg instead).

Without this normalization, deploying as-is on Render fails at startup
with either a pydantic validation error (wrong scheme) or an asyncpg
connection error (unrecognized `sslmode` parameter).
"""

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def normalize_database_url(raw: str) -> tuple[str, bool]:
    """
    Normalize a Postgres URL for SQLAlchemy + asyncpg.

    Returns (normalized_url, ssl_required).
    Accepts postgres://, postgresql://, or postgresql+asyncpg:// as input.
    """
    parts = urlsplit(raw)
    scheme = parts.scheme

    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    ssl_required = False
    filtered_query = []
    for key, value in query_pairs:
        if key.lower() == "sslmode":
            ssl_required = value.lower() in ("require", "verify-ca", "verify-full")
            continue
        filtered_query.append((key, value))

    normalized = urlunsplit((
        scheme,
        parts.netloc,
        parts.path,
        urlencode(filtered_query),
        parts.fragment,
    ))
    return normalized, ssl_required
