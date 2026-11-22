import pytest
from fastapi import status
from sqlalchemy import text

BASE = "/api/v1/cars"


def test_complete_crud(client, car_payload):
    created = client.post(BASE, json=car_payload)
    assert created.status_code == status.HTTP_201_CREATED
    car_id = created.json()["id"]
    assert created.headers["location"] == f"{BASE}/{car_id}"
    assert created.headers["x-request-id"]

    fetched = client.get(created.headers["location"])
    assert fetched.status_code == status.HTTP_200_OK
    assert fetched.json() == {"car": car_payload | {"id": car_id, "amount": 1}}

    updated = client.patch(f"{BASE}/{car_id}", json={"price": "50000.01"})
    assert updated.status_code == status.HTTP_204_NO_CONTENT
    assert updated.content == b""
    assert client.get(f"{BASE}/{car_id}").json()["car"]["price"] == "50000.01"
    assert client.get(f"{BASE}/{car_id}").json()["car"]["name"] == "Tesla"

    deleted = client.delete(f"{BASE}/{car_id}")
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert deleted.content == b""
    assert client.get(f"{BASE}/{car_id}").status_code == status.HTTP_404_NOT_FOUND


def test_empty_collection_and_no_matches(client, create_car):
    assert client.get(BASE).json() == {"cars": [], "next_cursor": None}
    create_car()

    response = client.get(BASE, params={"price_to": "1"})
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"cars": [], "next_cursor": None}


def test_price_filters_are_inclusive(client, create_car):
    create_car(price="100.00")
    target = create_car(price="200.00")
    create_car(price="300.00")
    assert len(client.get(BASE).json()["cars"]) == 3

    response = client.get(BASE, params={"price_from": "200", "price_to": "200"})
    assert [car["id"] for car in response.json()["cars"]] == [target]
    assert len(client.get(BASE, params={"price_to": "100"}).json()["cars"]) == 1
    assert len(client.get(BASE).json()["cars"]) == 3


def test_cursor_pagination_with_deleted_ids_and_exact_last_page(client, create_car):
    ids = [create_car(name=f"Car {index}") for index in range(6)]
    client.delete(f"{BASE}/{ids[1]}")
    client.delete(f"{BASE}/{ids[3]}")

    first = client.get(BASE, params={"limit": 2}).json()
    assert [car["id"] for car in first["cars"]] == [ids[0], ids[2]]
    assert first["next_cursor"] == ids[2]

    second = client.get(BASE, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [car["id"] for car in second["cars"]] == [ids[4], ids[5]]
    assert second["next_cursor"] is None
    assert client.get(BASE, params={"cursor": ids[-1]}).json()["cars"] == []


def test_default_page_size_and_custom_limit(client, create_car):
    for index in range(11):
        create_car(name=f"Car {index}")
    assert len(client.get(BASE).json()["cars"]) == 10
    assert len(client.get(BASE, params={"limit": 1}).json()["cars"]) == 1


def test_update_is_visible_in_first_page_and_item(client, create_car):
    ids = [create_car(name=f"Car {index}") for index in range(11)]
    boundary = ids[9]
    assert len(client.get(BASE).json()["cars"]) == 10

    client.get(f"{BASE}/{boundary}")
    assert (
        client.patch(f"{BASE}/{boundary}", json={"price": "123.45"}).status_code
        == status.HTTP_204_NO_CONTENT
    )

    cars = client.get(BASE).json()["cars"]
    assert next(car for car in cars if car["id"] == boundary)["price"] == "123.45"
    assert client.get(f"{BASE}/{boundary}").json()["car"]["price"] == "123.45"


def test_filtered_queries_reflect_writes(client, create_car):
    first = create_car(price="100.00")
    second = create_car(price="200.00")
    params = {"cursor": first, "price_to": "200.00", "limit": 1}
    assert client.get(BASE, params=params).json()["cars"][0]["id"] == second

    client.patch(f"{BASE}/{second}", json={"price": "300.00"})
    assert client.get(BASE, params=params).json()["cars"] == []

    third = create_car(price="150.00")
    assert client.get(BASE, params=params).json()["cars"][0]["id"] == third

    client.delete(f"{BASE}/{third}")
    assert client.get(BASE, params=params).json()["cars"] == []


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_missing_car(client, method):
    kwargs = {"json": {"name": "New name"}} if method == "patch" else {}
    response = getattr(client, method)(f"{BASE}/999", **kwargs)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"error": "not found", "details": ["car 999 not found"]}


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"name": "x" * 1025},
        {"colour": "#995GHA"},
        {"colour": "red"},
        {"price": "0"},
        {"price": "-1"},
        {"price": "1.001"},
        {"price": "100000000.00"},
        {"price": "NaN"},
        {"price": "Infinity"},
        {"build_date": "2022-02-30"},
        {"extra": "unknown field"},
    ],
)
def test_invalid_create(client, car_payload, changes):
    response = client.post(BASE, json=car_payload | changes)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"] == "validation error"
    assert client.get(BASE).json()["cars"] == []


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"price": None}, {"extra": 1}])
def test_invalid_patch_does_not_modify_car(client, create_car, payload):
    car_id = create_car()
    response = client.patch(f"{BASE}/{car_id}", json=payload)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert client.get(f"{BASE}/{car_id}").json()["car"]["price"] == "50000.00"


@pytest.mark.parametrize(
    "query",
    [
        {"price_from": "10", "price_to": "1"},
        {"price_from": "-1"},
        {"price_to": "NaN"},
        {"cursor": -1},
        {"cursor": "abc"},
        {"cursor": 9223372036854775808},
        {"limit": 0},
        {"limit": 101},
        {"unknown": "value"},
    ],
)
def test_invalid_query(client, query):
    assert client.get(BASE, params=query).status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.parametrize("car_id", ["abc", "-1", "0", "9223372036854775808"])
def test_invalid_id(client, car_id):
    assert client.get(f"{BASE}/{car_id}").status_code == status.HTTP_400_BAD_REQUEST


def test_malformed_json(client):
    response = client.post(BASE, content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_decimal_precision_and_trimmed_name(client, create_car):
    car_id = create_car(name="  My car  ", price="0.10")
    assert client.get(f"{BASE}/{car_id}").json()["car"]["price"] == "0.10"
    assert client.get(f"{BASE}/{car_id}").json()["car"]["name"] == "My car"


def test_health_and_openapi(client):
    assert client.get("/api/v1/healthcheck").json() == {
        "status": "ok",
        "database": "ok",
        "cache": "injected",
        "version": "1.0.0",
    }
    assert client.get("/swagger").status_code == status.HTTP_200_OK

    schema = client.get("/openapi.json").json()
    assert {"get", "post"} <= schema["paths"][BASE].keys()
    assert {"get", "patch", "delete"} <= schema["paths"][BASE + "/{car_id}"].keys()
    for path, operations in schema["paths"].items():
        if path.startswith(BASE):
            for operation in operations.values():
                responses = operation["responses"]
                assert str(status.HTTP_422_UNPROCESSABLE_CONTENT) not in responses
                assert responses[str(status.HTTP_400_BAD_REQUEST)]["content"]["application/json"][
                    "schema"
                ] == {"$ref": "#/components/schemas/ErrorResponse"}


def test_database_failure_is_reported_without_sql_details(client, engine):
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE cars"))

    response = client.get(BASE)
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"error": "database unavailable", "details": []}


def test_direct_database_changes_need_cache_invalidation(client, create_car, engine, cache):
    car_id = create_car()
    assert client.get(f"{BASE}/{car_id}").json()["car"]["name"] == "Tesla"
    assert client.get(BASE).json()["cars"][0]["name"] == "Tesla"

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE cars SET name='Changed directly' WHERE id=:id"), {"id": car_id}
        )
    assert client.get(f"{BASE}/{car_id}").json()["car"]["name"] == "Tesla"
    assert client.get(BASE).json()["cars"][0]["name"] == "Tesla"

    cache.invalidate(car_id)
    assert client.get(f"{BASE}/{car_id}").json()["car"]["name"] == "Changed directly"
    assert client.get(BASE).json()["cars"][0]["name"] == "Changed directly"
