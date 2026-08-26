import pytest

from common.db_url import normalize_database_url


def test_mysql_url_is_rejected_with_project_context_message():
    with pytest.raises(ValueError, match="requires PostgreSQL"):
        normalize_database_url("mysql://user:pass@localhost/other_project")


def test_postgresql_url_is_normalized_for_asyncpg():
    normalized, ssl_required = normalize_database_url(
        "postgresql://user:pass@localhost/afm?sslmode=require"
    )

    assert normalized == "postgresql+asyncpg://user:pass@localhost/afm"
    assert ssl_required is True


def test_asyncpg_url_is_preserved():
    normalized, ssl_required = normalize_database_url(
        "postgresql+asyncpg://user:pass@localhost/afm"
    )

    assert normalized == "postgresql+asyncpg://user:pass@localhost/afm"
    assert ssl_required is False
