from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.cache import RedisCache
from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration
BASE = "/api/v1/cars"


def create_car(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


def read_stock_and_logs(engine, car_id):
    with engine.connect() as connection:
        amount = connection.scalar(
            text("SELECT amount FROM core.car_stock WHERE car_id=:id"), {"id": car_id}
        )
        logs = connection.execute(
            text(
                "SELECT operation_type::text, amount_before, amount_after, occurred_at "
                "FROM core.car_operation_log WHERE car_id=:id ORDER BY id"
            ),
            {"id": car_id},
        ).all()
    return amount, logs


def test_buy_sell_and_operation_resource(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    assert read_stock_and_logs(pg_engine, car_id) == (1, [])
    sold = pg_client.post(f"{BASE}/{car_id}/sales")
    assert sold.status_code == status.HTTP_201_CREATED, sold.text
    operation = sold.json()
    assert operation["operation_type"] == "sell"
    assert (operation["amount_before"], operation["amount_after"]) == (1, 2)
    assert datetime.fromisoformat(operation["occurred_at"].replace("Z", "+00:00")).tzinfo
    assert sold.headers["location"] == f"{BASE}/{car_id}/operations/{operation['id']}"
    assert pg_client.get(sold.headers["location"]).json() == operation
    assert pg_client.get(f"{BASE}/{car_id}").json()["car"]["amount"] == 2

    bought = pg_client.post(f"{BASE}/{car_id}/purchases")
    assert bought.status_code == status.HTTP_201_CREATED
    assert bought.json()["operation_type"] == "buy"
    assert (bought.json()["amount_before"], bought.json()["amount_after"]) == (2, 1)
    assert pg_client.get(f"{BASE}/{car_id}").json()["car"]["amount"] == 1
    amount, logs = read_stock_and_logs(pg_engine, car_id)
    assert amount == 1
    assert [tuple(row[:3]) for row in logs] == [("sell", 1, 2), ("buy", 2, 1)]


def test_failed_buy_changes_neither_stock_nor_log(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    assert pg_client.post(f"{BASE}/{car_id}/purchases").status_code == status.HTTP_201_CREATED
    before = read_stock_and_logs(pg_engine, car_id)
    assert before[0] == 0
    assert pg_client.post(f"{BASE}/{car_id}/purchases").status_code == status.HTTP_409_CONFLICT
    assert read_stock_and_logs(pg_engine, car_id) == before


def test_operation_cannot_be_read_under_another_car(pg_client, car_payload):
    first = create_car(pg_client, car_payload)
    second = create_car(pg_client, car_payload)
    operation = pg_client.post(f"{BASE}/{first}/sales").json()
    assert (
        pg_client.get(f"{BASE}/{second}/operations/{operation['id']}").status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_stock_limit_is_a_conflict(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    with pg_engine.begin() as connection:
        connection.execute(
            text("UPDATE core.car_stock SET amount=2147483647 WHERE car_id=:id"), {"id": car_id}
        )
    before = read_stock_and_logs(pg_engine, car_id)
    response = pg_client.post(f"{BASE}/{car_id}/sales")
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["details"] == [f"stock limit reached for car {car_id}"]
    assert read_stock_and_logs(pg_engine, car_id) == before


def test_trigger_logs_direct_sql_changes_and_ignores_noop(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    with pg_engine.begin() as connection:
        connection.execute(
            text("UPDATE core.car_stock SET amount=amount+2 WHERE car_id=:id"), {"id": car_id}
        )
        connection.execute(
            text("UPDATE core.car_stock SET amount=amount WHERE car_id=:id"), {"id": car_id}
        )
        connection.execute(
            text("UPDATE core.car_stock SET amount=amount-1 WHERE car_id=:id"), {"id": car_id}
        )
    amount, logs = read_stock_and_logs(pg_engine, car_id)
    assert amount == 2
    assert [tuple(row[:3]) for row in logs] == [("sell", 1, 3), ("buy", 3, 2)]


def test_sql_rollback_also_rolls_back_trigger_log(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    with pg_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text("UPDATE core.car_stock SET amount=2 WHERE car_id=:id"), {"id": car_id}
        )
        assert connection.scalar(text("SELECT count(*) FROM core.car_operation_log")) == 1
        transaction.rollback()
    assert read_stock_and_logs(pg_engine, car_id) == (1, [])


def test_failed_trigger_insert_rolls_back_stock(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    assert pg_client.post(f"{BASE}/{car_id}/sales").status_code == status.HTTP_201_CREATED
    before = read_stock_and_logs(pg_engine, car_id)
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE core.car_operation_log ADD CONSTRAINT test_reject_buy "
                "CHECK (operation_type <> 'buy')"
            )
        )
    try:
        assert pg_client.post(f"{BASE}/{car_id}/purchases").status_code == status.HTTP_409_CONFLICT
        assert read_stock_and_logs(pg_engine, car_id) == before
    finally:
        with pg_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE core.car_operation_log DROP CONSTRAINT test_reject_buy")
            )


def test_database_rejects_negative_stock(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    with pytest.raises(IntegrityError), pg_engine.begin() as connection:
        connection.execute(
            text("UPDATE core.car_stock SET amount=-1 WHERE car_id=:id"), {"id": car_id}
        )
    assert read_stock_and_logs(pg_engine, car_id) == (1, [])


def test_database_operation_type_is_native_enum(pg_engine):
    with pg_engine.connect() as connection:
        values = connection.scalars(
            text("SELECT unnest(enum_range(NULL::core.car_operation_type))::text")
        ).all()
    assert values == ["buy", "sell"]
    with pytest.raises(DBAPIError), pg_engine.begin() as connection:
        connection.execute(text("SELECT 'unknown'::core.car_operation_type"))


def test_deleting_car_removes_stock_and_preserves_logs(pg_client, pg_engine, car_payload):
    car_id = create_car(pg_client, car_payload)
    operations = []
    for resource in ("sales", "purchases"):
        response = pg_client.post(f"{BASE}/{car_id}/{resource}")
        assert response.status_code == status.HTTP_201_CREATED, response.text
        operations.append((response.headers["location"], response.json()))
    _, logs_before = read_stock_and_logs(pg_engine, car_id)
    assert len(logs_before) == 2

    assert pg_client.delete(f"{BASE}/{car_id}").status_code == status.HTTP_204_NO_CONTENT
    assert pg_client.get(f"{BASE}/{car_id}").status_code == status.HTTP_404_NOT_FOUND
    assert read_stock_and_logs(pg_engine, car_id) == (None, logs_before)
    for location, operation in operations:
        response = pg_client.get(location)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == operation
        assert response.json()["car_id"] == car_id
    other_car_id = create_car(pg_client, car_payload)
    operation_id = operations[0][1]["id"]
    assert (
        pg_client.get(f"{BASE}/{other_car_id}/operations/{operation_id}").status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_concurrent_buyers_cannot_oversell(pg_client, pg_engine, real_redis, car_payload):
    car_id = create_car(pg_client, car_payload)

    def buy(_):
        with TestClient(
            create_app(
                Settings(_env_file=None), engine=pg_engine, cache=RedisCache(real_redis, ttl=60)
            )
        ) as client:
            return client.post(f"{BASE}/{car_id}/purchases").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(buy, range(20)))
    assert statuses.count(status.HTTP_201_CREATED) == 1
    assert statuses.count(status.HTTP_409_CONFLICT) == 19
    amount, logs = read_stock_and_logs(pg_engine, car_id)
    assert amount == 0
    assert [row.operation_type for row in logs] == ["buy"]


def test_concurrent_sellers_do_not_lose_increments(pg_client, pg_engine, real_redis, car_payload):
    car_id = create_car(pg_client, car_payload)

    def sell(_):
        with TestClient(
            create_app(
                Settings(_env_file=None), engine=pg_engine, cache=RedisCache(real_redis, ttl=60)
            )
        ) as client:
            return client.post(f"{BASE}/{car_id}/sales").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(sell, range(20)))
    assert statuses == [status.HTTP_201_CREATED] * 20
    amount, logs = read_stock_and_logs(pg_engine, car_id)
    assert amount == 21
    assert [(row.amount_before, row.amount_after) for row in logs] == [
        (i, i + 1) for i in range(1, 21)
    ]


def test_second_sql_script_upgrades_existing_catalog(pg_engine):
    scripts = Path(__file__).resolve().parents[2] / "database" / "init"
    # Only the dedicated carshop_test database is exposed by pg_engine's guarded fixture.
    with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("DROP SCHEMA core CASCADE")
        connection.exec_driver_sql(
            (scripts / "001_create_cars.sql").read_text(), execution_options={"no_parameters": True}
        )
        connection.exec_driver_sql(
            "INSERT INTO core.cars(name,colour,price,build_date) "
            "VALUES ('Existing car','#123ABC',123.45,'2022-01-01')"
        )
        connection.exec_driver_sql(
            (scripts / "002_car_stock.sql").read_text(), execution_options={"no_parameters": True}
        )
        assert connection.scalar(text("SELECT name FROM core.cars")) == "Existing car"
        assert connection.scalar(text("SELECT amount FROM core.car_stock")) == 1
        assert connection.scalar(text("SELECT count(*) FROM core.car_operation_log")) == 0
    pg_engine.dispose()
