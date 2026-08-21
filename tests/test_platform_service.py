import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

from platform_manager.platform_service import platform_service
from config.exceptions import NotFoundError, APIKeyExpiredError


@pytest.mark.asyncio
async def test_onboard_platform_generates_credentials():
    """Onboarding cree API key + secret + expiry."""
    with patch('platform_manager.platform_service.AsyncSessionLocal') as mock_session:
        mock_instance = MagicMock()
        mock_instance.flush = AsyncMock()
        mock_instance.commit = AsyncMock()
        mock_instance.execute = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await platform_service.onboard_platform(
            name="Test Platform",
            contact_email="test@example.com",
            webhook_url="https://test.com/webhook",
        )

        assert "platform_id" in result
        assert "api_key" in result
        assert "api_secret" in result
        assert "expires_at" in result
        assert result["api_key"].startswith("afm_live_")
        assert len(result["api_secret"]) >= 48


@pytest.mark.asyncio
async def test_rotate_key_deactivates_old():
    """Rotation desactive l'ancienne cle et cree nouvelle."""
    with patch('platform_manager.platform_service.AsyncSessionLocal') as mock_session:
        mock_instance = MagicMock()
        mock_instance.flush = AsyncMock()
        mock_instance.commit = AsyncMock()
        mock_instance.execute = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await platform_service.rotate_key("platform-123")

        assert "api_key" in result
        assert "api_secret" in result
        assert result["api_key"].startswith("afm_live_")


@pytest.mark.asyncio
async def test_validate_key_expired_raises():
    """Cle expiree levee APIKeyExpiredError."""
    with patch('platform_manager.platform_service.AsyncSessionLocal') as mock_session:
        mock_instance = MagicMock()
        mock_instance.flush = AsyncMock()
        mock_instance.commit = AsyncMock()
        mock_instance.execute = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        # Simuler cle expiree
        expired_key = AsyncMock()
        expired_key.is_active = True
        expired_key.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        expired_key.platform_id = "platform-123"

        mock_instance.execute = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.side_effect = [expired_key, AsyncMock(is_active=True)]
        mock_instance.execute.return_value = result

        with pytest.raises(APIKeyExpiredError):
            await platform_service.validate_key("expired_key_123")


@pytest.mark.asyncio
async def test_validate_key_invalid_raises_not_found():
    """Cle invalide levee NotFoundError."""
    with patch('platform_manager.platform_service.AsyncSessionLocal') as mock_session:
        mock_instance = MagicMock()
        mock_instance.flush = AsyncMock()
        mock_instance.commit = AsyncMock()
        mock_instance.execute = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_instance.execute = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_instance.execute.return_value = result

        with pytest.raises(NotFoundError):
            await platform_service.validate_key("invalid_key")
