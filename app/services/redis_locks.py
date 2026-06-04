"""Small Redis owner-token locks for cross-process background coordination."""

from __future__ import annotations

import logging
import uuid

from app.db.session import InMemoryRedis, redis_client

logger = logging.getLogger(__name__)

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


async def acquire_redis_lock(key: str, *, ttl_sec: int, token: str | None = None) -> str | None:
    owner = token or uuid.uuid4().hex
    try:
        claimed = await redis_client.set(key, owner, nx=True, ex=int(ttl_sec))
        return owner if claimed else None
    except Exception as exc:
        logger.warning("redis_lock.acquire_failed key=%s err=%s", key, exc)
        return None


async def release_redis_lock(key: str, token: str) -> bool:
    try:
        if isinstance(redis_client, InMemoryRedis):
            if await redis_client.get(key) == token:
                await redis_client.delete(key)
                return True
            return False
        result = await redis_client.eval(_RELEASE_LUA, 1, key, token)
        return bool(int(result or 0))
    except Exception as exc:
        logger.warning("redis_lock.release_failed key=%s err=%s", key, exc)
        return False
