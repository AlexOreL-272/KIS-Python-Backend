import os

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.cache import CACHE_PREFIX, RedisCache
from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="session")
def integration_database():
    url = os.getenv(
        "TEST_DATABASE_URL", "postgresql+psycopg://carshop:carshop@localhost:55432/carshop_test"
    )

    if make_url(url).database != "carshop_test":
        pytest.fail("Integration tests require a dedicated database named carshop_test")

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == "carshop_test"
        if connection.scalar(text("SELECT to_regclass('core.car_stock')")) is None:
            pytest.fail(
                "Missing schema: initialize PostgreSQL with database/init/*.sql in filename order"
            )

    yield engine
    engine.dispose()


@pytest.fixture
def pg_engine(integration_database):
    engine = integration_database
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE core.car_operation_log, core.cars RESTART IDENTITY CASCADE")
        )

    yield engine

    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE core.car_operation_log, core.cars RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def real_redis():
    url = os.getenv("TEST_REDIS_URL", "redis://localhost:56379/15")
    with Redis.from_url(url, decode_responses=True, socket_timeout=1) as client:
        client.ping()
        keys = list(client.scan_iter(f"{CACHE_PREFIX}:*"))
        if keys:
            client.delete(*keys)

        yield client

        keys = list(client.scan_iter(f"{CACHE_PREFIX}:*"))
        if keys:
            client.delete(*keys)


@pytest.fixture
def pg_client(pg_engine, real_redis):
    app = create_app(
        Settings(_env_file=None), engine=pg_engine, cache=RedisCache(real_redis, ttl=60)
    )
    with TestClient(app) as client:
        yield client
