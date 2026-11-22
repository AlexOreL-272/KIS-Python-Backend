from datetime import UTC, datetime

import pytest
from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import CarOperationType
from app.models import CarOperationLog, CarStock

BASE = "/api/v1/cars"


def test_new_car_has_one_in_stock_only_in_detail(client, create_car, engine):
    car_id = create_car()
    assert client.get(f"{BASE}/{car_id}").json()["car"]["amount"] == 1
    assert "amount" not in client.get(BASE).json()["cars"][0]
    with Session(engine) as session:
        assert session.scalar(select(CarStock.amount).where(CarStock.car_id == car_id)) == 1


def test_buy_without_stock_is_conflict(client, create_car, engine):
    car_id = create_car()
    with Session(engine) as session:
        session.get(CarStock, car_id).amount = 0
        session.commit()

    response = client.post(f"{BASE}/{car_id}/purchases")
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"error": "conflict", "details": [f"car {car_id} is out of stock"]}
    assert client.get(f"{BASE}/{car_id}").json()["car"]["amount"] == 0


@pytest.mark.parametrize("resource", ["purchases", "sales"])
def test_operation_for_missing_car(client, resource):
    assert client.post(f"{BASE}/999/{resource}").status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize("resource", ["purchases", "sales"])
@pytest.mark.parametrize("car_id", ["0", "-1", "abc", "9223372036854775808"])
def test_operation_validates_id(client, resource, car_id):
    assert client.post(f"{BASE}/{car_id}/{resource}").status_code == status.HTTP_400_BAD_REQUEST


def test_missing_operation_is_not_found(client, create_car):
    car_id = create_car()
    assert client.get(f"{BASE}/{car_id}/operations/999").status_code == status.HTTP_404_NOT_FOUND


def test_stock_cannot_be_patched_as_catalog_data(client, create_car):
    car_id = create_car()
    assert (
        client.patch(f"{BASE}/{car_id}", json={"amount": 10}).status_code
        == status.HTTP_400_BAD_REQUEST
    )


def test_stock_routes_are_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert str(status.HTTP_201_CREATED) in paths[BASE + "/{car_id}/purchases"]["post"]["responses"]
    assert str(status.HTTP_409_CONFLICT) in paths[BASE + "/{car_id}/sales"]["post"]["responses"]
    assert "get" in paths[BASE + "/{car_id}/operations/{operation_id}"]


def test_orm_log_survives_car_deletion(client, create_car, engine):
    car_id = create_car()
    with Session(engine) as session:
        operation = CarOperationLog(
            car_id=car_id,
            operation_type=CarOperationType.SELL,
            amount_before=1,
            amount_after=2,
            occurred_at=datetime.now(UTC),
        )
        session.add(operation)
        session.commit()
        operation_id = operation.id

    assert client.delete(f"{BASE}/{car_id}").status_code == status.HTTP_204_NO_CONTENT
    with Session(engine) as session:
        assert session.get(CarStock, car_id) is None
        operation = session.get(CarOperationLog, operation_id)
        assert operation is not None
        assert operation.car_id == car_id
        assert operation.operation_type == CarOperationType.SELL
        assert (operation.amount_before, operation.amount_after) == (1, 2)
