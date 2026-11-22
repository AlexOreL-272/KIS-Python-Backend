import os
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.cache import RedisCache
from app.config import Settings
from app.main import create_app
from scripts.seed import main as seed

pytestmark = pytest.mark.integration
BASE = "/api/v1/cars"


def test_production_lifespan_connects_and_closes_database(pg_engine, real_redis, car_payload):
    settings = Settings(
        _env_file=None,
        database_url=pg_engine.url.render_as_string(hide_password=False),
        redis_url=os.getenv("TEST_REDIS_URL", "redis://localhost:56379/15"),
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/healthcheck").json() == {
            "status": "ok",
            "database": "ok",
            "cache": "ok",
            "version": "1.0.0",
        }
        assert client.post(BASE, json=car_payload).status_code == status.HTTP_201_CREATED
        assert client.get(BASE).status_code == status.HTTP_200_OK


def test_crud_persists_exact_money_and_dates(pg_client, pg_engine, car_payload):
    created = pg_client.post(BASE, json=car_payload | {"price": "12345.67"})
    assert created.status_code == status.HTTP_201_CREATED
    car_id = created.json()["id"]
    with pg_engine.connect() as connection:
        row = connection.execute(
            text("SELECT price, build_date, created_at FROM core.cars WHERE id=:id"), {"id": car_id}
        ).one()
    assert str(row.price) == "12345.67"
    assert row.build_date.isoformat() == "2022-01-01"
    assert row.created_at.tzinfo is not None
    assert (
        pg_client.patch(f"{BASE}/{car_id}", json={"price": "0.10"}).status_code
        == status.HTTP_204_NO_CONTENT
    )
    assert pg_client.get(f"{BASE}/{car_id}").json()["car"]["price"] == "0.10"
    assert pg_client.delete(f"{BASE}/{car_id}").status_code == status.HTTP_204_NO_CONTENT
    assert pg_client.get(f"{BASE}/{car_id}").status_code == status.HTTP_404_NOT_FOUND


def test_filtered_pages_reflect_writes_between_app_instances(
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

    assert pg_client.get(BASE).json()["cars"][9]["price"] == "100.00"
    assert pg_client.get(f"{BASE}/{ids[9]}").json()["car"]["price"] == "100.00"
    assert pg_client.get(BASE, params=params).json()["cars"][0]["name"] == "Changed"
    assert len(pg_client.get(BASE, params={"price_to": "100"}).json()["cars"]) == 1


@pytest.mark.parametrize("column,value", [("price", "-1"), ("name", "   "), ("colour", "#995GHA")])
def test_database_itself_rejects_invalid_rows(pg_engine, column, value):
    values = {"name": "Car", "colour": "#000000", "price": "100.00", "build_date": "2022-01-01"}
    values[column] = value
    with pytest.raises(IntegrityError), pg_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO core.cars(name, colour, price, build_date) "
                "VALUES (:name, :colour, :price, :build_date)"
            ),
            values,
        )


def test_seed_is_repeatable_and_preserves_valid_colours(pg_engine, real_redis):
    url = pg_engine.url.render_as_string(hide_password=False)
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": url,
            "REDIS_URL": os.getenv("TEST_REDIS_URL", "redis://localhost:56379/15"),
        },
    ):
        seed()
        seed()

    with pg_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM core.cars")) == 25
        assert (
            connection.scalar(text("SELECT colour FROM core.cars WHERE name='Geely'")) == "#995AAA"
        )


def test_postgres_init_creates_expected_schema(pg_engine):
    inspector = inspect(pg_engine)
    columns = {column["name"]: column for column in inspector.get_columns("cars", schema="core")}
    assert set(columns) == {"id", "name", "colour", "price", "build_date", "created_at"}
    assert all(not column["nullable"] for column in columns.values())
    assert columns["price"]["type"].precision == 10
    assert columns["price"]["type"].scale == 2
    assert inspector.get_pk_constraint("cars", schema="core")["constrained_columns"] == ["id"]
    assert {check["name"] for check in inspector.get_check_constraints("cars", schema="core")} == {
        "ck_cars_positive_price",
        "ck_cars_nonempty_name",
        "ck_cars_colour",
    }
    assert not inspector.has_table("alembic_version")
