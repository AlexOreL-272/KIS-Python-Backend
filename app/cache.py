import json
import logging
from typing import Protocol
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError, WatchError

logger = logging.getLogger(__name__)

CACHE_PREFIX = "carshop:v3"
LIST_PREFIX = f"{CACHE_PREFIX}:cars"
CAR_PREFIX = f"{CACHE_PREFIX}:car"


class Cache(Protocol):
    def token(self) -> str | None: ...
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, token: str | None) -> None: ...
    def invalidate(self, car_id: int | None = None, *, lists: bool = True) -> None: ...


class RedisCache:
    WRITE_TOKEN_KEY = f"{CACHE_PREFIX}:write_token"

    def __init__(self, client: Redis, ttl: int):
        self.client = client
        self.ttl = ttl

    def token(self) -> str | None:
        try:
            current = self.client.get(self.WRITE_TOKEN_KEY)
            if current is None:
                self.client.set(self.WRITE_TOKEN_KEY, uuid4().hex, nx=True)
                current = self.client.get(self.WRITE_TOKEN_KEY)
            return current
        except RedisError:
            logger.warning("Redis unavailable; bypassing cache")
            return None

    def get(self, key: str) -> str | None:
        try:
            return self.client.get(key)
        except RedisError:
            logger.warning("Redis read failed; using PostgreSQL")
            return None

    def set(self, key: str, value: str, token: str | None) -> None:
        if token is None:
            return
        try:
            with self.client.pipeline() as pipe:
                pipe.watch(self.WRITE_TOKEN_KEY)
                if pipe.get(self.WRITE_TOKEN_KEY) != token:
                    return
                pipe.multi()
                pipe.set(key, value, ex=self.ttl)
                pipe.execute()
        except WatchError:
            pass
        except RedisError:
            logger.warning("Redis write failed; response is served from PostgreSQL")

    def invalidate(self, car_id: int | None = None, *, lists: bool = True) -> None:
        try:
            self.client.set(self.WRITE_TOKEN_KEY, uuid4().hex)
            if car_id is None:
                keys = list(self.client.scan_iter(match=f"{CAR_PREFIX}:*", count=100))
            else:
                keys = [f"{CAR_PREFIX}:{car_id}"]
            pages = self.client.scan_iter(match=f"{LIST_PREFIX}:*", count=100) if lists else ()
            for key in pages:
                if car_id is None or self._page_affected(key, car_id):
                    keys.append(key)
            if keys:
                self.client.delete(*keys)
        except RedisError:
            logger.warning("Cache invalidation failed; old values expire within the cache TTL")

    def _page_affected(self, key: str, car_id: int) -> bool:
        try:
            cursor = int(key.rsplit(":", 2)[1])
            if car_id <= cursor:
                return False
            value = self.client.get(key)
            if value is None:
                return False
            end_id = json.loads(value)["end_id"]
            if end_id is not None and type(end_id) is not int:
                return True
            return end_id is None or car_id <= end_id
        except (ValueError, KeyError, TypeError):
            return True
