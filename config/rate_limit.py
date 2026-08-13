"""Redis-based distributed rate limiting."""

import time

import redis.asyncio as redis

from config.config import get_settings
from config.exceptions import RateLimitError

settings = get_settings()


class RateLimiter:
    def __init__(self):
        self._redis = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(
                settings.redis_url,
                max_connections=settings.redis_pool_size,
                decode_responses=True,
            )
        return self._redis

    async def is_allowed(self, key: str, limit: int, window_seconds: int) -> bool:
        redis_client = await self._get_redis()
        now = time.time()
        window_start = now - window_seconds

        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(f"ratelimit:{key}", 0, window_start)
        pipe.zcard(f"ratelimit:{key}")
        pipe.zadd(f"ratelimit:{key}", {str(now): now})
        pipe.expire(f"ratelimit:{key}", window_seconds + 1)

        results = await pipe.execute()
        current_count = results[1]

        if current_count >= limit:
            await redis_client.zrem(f"ratelimit:{key}", str(now))
            raise RateLimitError(
                f"Rate limit exceeded: {limit} requests per {window_seconds}s"
            )
        return True

    async def close(self):
        if self._redis:
            await self._redis.close()


rate_limiter = RateLimiter()
