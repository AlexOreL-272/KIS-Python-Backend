import fakeredis
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from app.cache import RedisCache
from app.config import Settings
from app.main import create_app
from app.models import Base


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", help="Run dedicated service tests")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        return
    skip = pytest.mark.skip(reason="use --integration with dedicated PostgreSQL")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def engine():
    db_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"core": None}},
    )

    @event.listens_for(db_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(db_engine)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def redis_client():
    with fakeredis.FakeRedis(decode_responses=True) as client:
        yield client


@pytest.fixture
def cache(redis_client):
    return RedisCache(redis_client, ttl=60)


@pytest.fixture
def app(engine, cache):
    return create_app(Settings(_env_file=None), engine=engine, cache=cache)


@pytest.fixture
def client(app):
    with TestClient(app) as api_client:
        yield api_client


@pytest.fixture
def car_payload():
    return {"name": "Tesla", "colour": "#008000", "price": "50000.00", "build_date": "2022-01-01"}


@pytest.fixture
def create_car(client, car_payload):
    def create(**changes):
        response = client.post("/api/v1/cars", json=car_payload | changes)
        assert response.status_code == status.HTTP_201_CREATED, response.text
        return response.json()["id"]

    return create
