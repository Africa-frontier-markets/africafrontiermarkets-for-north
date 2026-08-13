"""Platform management with API key rotation. V45: timedelta imported."""

from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update

from config.database import AsyncSessionLocal
from config.exceptions import NotFoundError, APIKeyExpiredError
from config.security import generate_api_key, generate_api_secret
from platform_manager.models import Platform, APIKey


class PlatformService:
    KEY_VALIDITY_DAYS = 90

    async def onboard_platform(self, name: str, contact_email: str, webhook_url: str | None = None) -> dict:
        async with AsyncSessionLocal() as session:
            platform = Platform(
                name=name,
                contact_email=contact_email,
                webhook_url=webhook_url,
            )
            session.add(platform)
            await session.flush()

            api_key = APIKey(
                platform_id=platform.id,
                key_value=generate_api_key(),
                secret_hash=generate_api_secret(),
                expires_at=datetime.now(timezone.utc) + timedelta(days=self.KEY_VALIDITY_DAYS),
            )
            session.add(api_key)
            await session.commit()

            return {
                "platform_id": str(platform.id),
                "api_key": api_key.key_value,
                "api_secret": api_key.secret_hash,
                "expires_at": api_key.expires_at.isoformat(),
            }

    async def rotate_key(self, platform_id: str) -> dict:
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(APIKey)
                .where(APIKey.platform_id == platform_id, APIKey.is_active == True)
                .values(is_active=False, revoked_at=datetime.now(timezone.utc))
            )

            new_key = APIKey(
                platform_id=platform_id,
                key_value=generate_api_key(),
                secret_hash=generate_api_secret(),
                expires_at=datetime.now(timezone.utc) + timedelta(days=self.KEY_VALIDITY_DAYS),
            )
            session.add(new_key)
            await session.commit()

            return {
                "api_key": new_key.key_value,
                "api_secret": new_key.secret_hash,
                "expires_at": new_key.expires_at.isoformat(),
            }

    async def validate_key(self, key_value: str) -> Platform:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(APIKey).where(APIKey.key_value == key_value, APIKey.is_active == True)
            )
            api_key = result.scalar_one_or_none()

            if not api_key:
                raise NotFoundError("Invalid API key")

            if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
                raise APIKeyExpiredError(f"API key expired on {api_key.expires_at.isoformat()}")

            result = await session.execute(
                select(Platform).where(Platform.id == api_key.platform_id)
            )
            platform = result.scalar_one_or_none()

            if not platform or not platform.is_active:
                raise NotFoundError("Platform not found or inactive")

            return platform


platform_service = PlatformService()
