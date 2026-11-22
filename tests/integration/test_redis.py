import os
import time
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.cache import CACHE_PREFIX, RedisCache
from app.config import Settings
from app.main import create_app
from app.schemas import CarFilters
from app.services.cars import car_cache_key, list_cache_key
from scripts.seed import main as seed

pytestmark = pytest.mark.integration
BASE = "/api/v1/cars"


def test_real_redis_expiration(real_redis):
    cache = RedisCache(real_redis, ttl=1)
    key = f"{CACHE_PREFIX}:expiration-test"
    cache.set(key, "value", cache.token())
    assert 0 < real_redis.pttl(key) <= 1000
    deadline = time.monotonic() + 3
    while real_redis.exists(key) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert cache.get(key) is None


def test_filtered_pages_and_cache_invalidation_between_app_instances(
    pg_client, pg_engine, real_redis, car_payload
):
    ids = []
    for index in range(12):
        created = pg_client.post(BASE, json=car_payload | {"name": f"Car {index}"})
        ids.append(created.json()["id"])
    assert len(pg_client.get(BASE).json()["cars"]) == 10
    assert pg_client.get(BASE, params={"price_to": "1"}).json()["cars"] == []
    params = {"cursor": ids[2], "limit": 3}
    assert pg_client.get(BASE, params=params).json()["cars"][0]["name"] == "Car 3"
    pg_client.get(f"{BASE}/{ids[9]}")
    pg_client.get(f"{BASE}/{ids[0]}")
    pg_client.get(BASE, params={"limit": 1})
    untouched_page_key = list_cache_key(CarFilters(limit=1))
    untouched_page = real_redis.get(untouched_page_key)
    untouched_car = real_redis.get(car_cache_key(ids[0]))
    other_app = create_app(
        Settings(_env_file=None), engine=pg_engine, cache=RedisCache(real_redis, ttl=60)
    )
    with TestClient(other_app) as other:
        assert (
            other.patch(f"{BASE}/{ids[9]}", json={"price": "100.00"}).status_code
            == status.HTTP_204_NO_CONTENT
        )
        assert (
            other.patch(f"{BASE}/{ids[3]}", json={"name": "Changed"}).status_code
            == status.HTTP_204_NO_CONTENT
        )
    assert real_redis.get(untouched_page_key) == untouched_page
    assert real_redis.get(car_cache_key(ids[0])) == untouched_car
    assert pg_client.get(BASE).json()["cars"][9]["price"] == "100.00"
    assert pg_client.get(f"{BASE}/{ids[9]}").json()["car"]["price"] == "100.00"
    assert pg_client.get(BASE, params=params).json()["cars"][0]["name"] == "Changed"
    assert len(pg_client.get(BASE, params={"price_to": "100"}).json()["cars"]) == 1


def test_stock_changes_invalidate_only_car_detail(pg_client, real_redis, car_payload):
    car_id = pg_client.post(BASE, json=car_payload).json()["id"]
    other_id = pg_client.post(BASE, json=car_payload).json()["id"]
    pg_client.get(BASE)
    pg_client.get(f"{BASE}/{car_id}")
    pg_client.get(f"{BASE}/{other_id}")
    page_key = list_cache_key(CarFilters())
    detail_key = car_cache_key(car_id)
    other_key = car_cache_key(other_id)
    page = real_redis.get(page_key)
    other = real_redis.get(other_key)

    for resource, expected_amount in (("sales", 2), ("purchases", 1), ("purchases", 0)):
        response = pg_client.post(f"{BASE}/{car_id}/{resource}")
        assert response.status_code == status.HTTP_201_CREATED, response.text
        assert not real_redis.exists(detail_key)
        assert real_redis.get(page_key) == page
        assert real_redis.get(other_key) == other
        assert pg_client.get(f"{BASE}/{car_id}").json()["car"]["amount"] == expected_amount

    detail = real_redis.get(detail_key)
    token = real_redis.get(RedisCache.WRITE_TOKEN_KEY)
    assert pg_client.post(f"{BASE}/{car_id}/purchases").status_code == status.HTTP_409_CONFLICT
    assert real_redis.get(detail_key) == detail
    assert real_redis.get(page_key) == page
    assert real_redis.get(RedisCache.WRITE_TOKEN_KEY) == token


def test_failed_trigger_keeps_cached_detail(pg_client, pg_engine, real_redis, car_payload):
    car_id = pg_client.post(BASE, json=car_payload).json()["id"]
    pg_client.get(f"{BASE}/{car_id}")
    key = car_cache_key(car_id)
    before = real_redis.get(key)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE core.car_operation_log ADD CONSTRAINT test_cache_reject_buy "
                "CHECK (operation_type <> 'buy')"
            )
        )
    try:
        assert pg_client.post(f"{BASE}/{car_id}/purchases").status_code == status.HTTP_409_CONFLICT
        assert real_redis.get(key) == before
        assert pg_client.get(f"{BASE}/{car_id}").json()["car"]["amount"] == 1
    finally:
        with pg_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE core.car_operation_log DROP CONSTRAINT test_cache_reject_buy")
            )


def test_seed_clears_cached_empty_catalog(pg_client, pg_engine, real_redis):
    assert pg_client.get(BASE).json()["cars"] == []
    key = list_cache_key(CarFilters())
    assert real_redis.exists(key)
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": pg_engine.url.render_as_string(hide_password=False),
            "REDIS_URL": os.getenv("TEST_REDIS_URL", "redis://localhost:56379/15"),
        },
    ):
        seed()
        assert not real_redis.exists(key)
        assert len(pg_client.get(BASE).json()["cars"]) == 10
        before = real_redis.get(key)
        seed()
        assert real_redis.get(key) == before
