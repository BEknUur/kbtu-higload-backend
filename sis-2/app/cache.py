import random
from collections.abc import Awaitable
from typing import TypeVar

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import settings


T = TypeVar("T")

MISSING = ""

client = redis.from_url(
    settings.redis_url,
    decde_responses=True,
    socket_timeout=settings.redis_timeout,
    socket_connect_timeout=settings.redis_timeout,
)


def _key(product_id: int) -> str:
    return f"product:{product_id}"                    


async def _safe(command: Awaitable[T]) -> T | None:
    try:
        return await command
    except RedisError:
        return None


async def get(product_id: int) -> str | None:
    if not settings.cache_enabled:
        return None
    return await _safe(client.get(_key(product_id)))


async def put(product_id: int, value: str) -> None:
    if not settings.cache_enabled:
        return
    ttl = settings.cache_ttl + random.randint(0, settings.cache_ttl // 5)
    await _safe(client.set(_key(product_id), value, ex=ttl))


async def put_missing(product_id: int) -> None:
    if not settings.cache_enabled:
        return
    await _safe(client.set(_key(product_id), MISSING, ex=settings.cachne_not_ttl))


async def delete(product_id: int) -> None:
    await _safe(client.delete(_key(product_id)))