from unittest.mock import Mock

import pytest
from fastapi import status
from redis.exceptions import ConnectionError, WatchError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cache import CACHE_PREFIX, RedisCache
from app.repositories.cars import SQLCarRepository
from app.schemas import CarFilters, CarUpdate
from app.services.cars import CachedCarList, CarService, car_cache_key, list_cache_key

BASE = "/api/v1/cars"


def test_cache_hit_does_not_query_database(client, create_car, engine):
    create_car()
    expected = client.get(BASE).json()
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE cars"))
    assert client.get(BASE).json() == expected


@pytest.mark.parametrize(
    "filters",
    [
        {"price_from": "0"},
        {"price_to": "100000"},
        {"price_from": "1", "price_to": "100000", "limit": 1},
    ],
)
def test_filtered_lists_bypass_all_cache_operations(app, client, create_car, filters):
    create_car()
    client.get(BASE)
    original_cache = app.state.cache
    forbidden_cache = Mock()
    app.state.cache = forbidden_cache
    try:
        response = client.get(BASE, params=filters)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["cars"]) == 1
        assert forbidden_cache.mock_calls == []
    finally:
        app.state.cache = original_cache


def test_pagination_only_requests_reuse_cache(client, create_car, engine):
    ids = [create_car() for _ in range(4)]
    params = {"cursor": ids[0], "limit": 2}
    expected = client.get(BASE, params=params).json()
    assert expected["next_cursor"] == ids[2]

    with engine.begin() as connection:
        connection.execute(text("DROP TABLE cars"))
    assert client.get(BASE, params=params).json() == expected


def test_cache_entries_have_real_ttl(client, create_car, redis_client):
    car_id = create_car()
    client.get(BASE)
    client.get(f"{BASE}/{car_id}")
    keys = list(redis_client.scan_iter(f"{CACHE_PREFIX}:*"))
    entries = [key for key in keys if key != RedisCache.WRITE_TOKEN_KEY]
    assert len(entries) == 2
    assert all(0 < redis_client.ttl(key) <= 60 for key in entries)


def test_expired_entry_is_reloaded(client, create_car, redis_client):
    create_car()
    expected = client.get(BASE).json()
    key = list_cache_key(CarFilters())
    redis_client.pexpire(key, 0)
    assert not redis_client.exists(key)
    assert client.get(BASE).json() == expected
    assert redis_client.ttl(key) > 0


@pytest.mark.parametrize("corruption", ["not json", '{"response": "invalid", "end_id": null}'])
def test_corrupt_cache_is_repaired(client, create_car, redis_client, corruption):
    create_car()
    expected = client.get(BASE).json()
    key = list_cache_key(CarFilters())
    redis_client.set(key, corruption)
    assert client.get(BASE).json() == expected
    assert CachedCarList.model_validate_json(redis_client.get(key)).response.cars
    assert redis_client.ttl(key) > 0


def test_update_keeps_unrelated_cards_and_pages(client, create_car, redis_client):
    ids = [create_car(name=f"Car {index}") for index in range(8)]
    first_filters = CarFilters(limit=2)
    later_filters = CarFilters(cursor=ids[4], limit=2)
    first_key = list_cache_key(first_filters)
    later_key = list_cache_key(later_filters)
    client.get(BASE, params=first_filters.model_dump(exclude_none=True))
    client.get(BASE, params=later_filters.model_dump(exclude_none=True))
    client.get(f"{BASE}/{ids[0]}")
    client.get(f"{BASE}/{ids[5]}")
    first_value = redis_client.get(first_key)
    unrelated_card = redis_client.get(car_cache_key(ids[0]))
    client.patch(f"{BASE}/{ids[5]}", json={"price": "123.45"})
    assert redis_client.get(first_key) == first_value
    assert redis_client.get(car_cache_key(ids[0])) == unrelated_card
    assert not redis_client.exists(later_key)
    assert not redis_client.exists(car_cache_key(ids[5]))
    assert (
        client.get(BASE, params=later_filters.model_dump(exclude_none=True)).json()["cars"][0][
            "price"
        ]
        == "123.45"
    )


def test_delete_with_gaps_invalidates_overlapping_pages_and_lookahead(
    client, create_car, redis_client
):
    ids = [create_car() for _ in range(7)]
    client.delete(f"{BASE}/{ids[1]}")
    client.delete(f"{BASE}/{ids[3]}")
    params = [{"limit": 2}, {"cursor": ids[0], "limit": 2}, {"cursor": ids[4], "limit": 1}]
    for query in params:
        client.get(BASE, params=query)
    untouched_key = list_cache_key(CarFilters(**params[2]))
    untouched = redis_client.get(untouched_key)

    client.delete(f"{BASE}/{ids[2]}")
    assert not redis_client.exists(list_cache_key(CarFilters(**params[0])))
    assert not redis_client.exists(list_cache_key(CarFilters(**params[1])))
    assert redis_client.get(untouched_key) == untouched

    first = client.get(BASE, params=params[0]).json()
    assert [car["id"] for car in first["cars"]] == [ids[0], ids[4]]


def test_deleting_only_lookahead_updates_next_cursor(client, create_car, redis_client):
    ids = [create_car() for _ in range(3)]
    assert client.get(BASE, params={"limit": 2}).json()["next_cursor"] == ids[1]

    client.delete(f"{BASE}/{ids[2]}")
    assert not redis_client.exists(list_cache_key(CarFilters(limit=2)))
    assert client.get(BASE, params={"limit": 2}).json()["next_cursor"] is None


def test_create_keeps_full_pages_and_invalidates_last_and_empty_pages(
    client, create_car, redis_client
):
    ids = [create_car() for _ in range(3)]
    queries = [{"limit": 1}, {"cursor": ids[1], "limit": 1}, {"cursor": ids[-1]}]
    for query in queries:
        client.get(BASE, params=query)

    full_key = list_cache_key(CarFilters(**queries[0]))
    full_value = redis_client.get(full_key)

    new_id = create_car()
    assert redis_client.get(full_key) == full_value
    assert not redis_client.exists(list_cache_key(CarFilters(**queries[1])))
    assert not redis_client.exists(list_cache_key(CarFilters(**queries[2])))

    last = client.get(BASE, params=queries[1]).json()
    assert last["next_cursor"] == ids[-1]
    assert client.get(BASE, params=queries[2]).json()["cars"][0]["id"] == new_id


def test_create_can_fill_an_id_gap(client, create_car, engine, cache, redis_client):
    ids = [create_car() for _ in range(4)]
    client.delete(f"{BASE}/{ids[1]}")
    client.get(BASE, params={"limit": 2})

    key = list_cache_key(CarFilters(limit=2))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cars(id,name,colour,price,build_date) "
                "VALUES (:id,'Late commit','#123ABC',100,'2022-01-01')"
            ),
            {"id": ids[1]},
        )

    cache.invalidate(ids[1])
    assert not redis_client.exists(key)
    assert [car["id"] for car in client.get(BASE, params={"limit": 2}).json()["cars"]] == ids[:2]


def test_redis_outage_does_not_break_crud(app, client, create_car):
    failing_client = Mock()
    failing_client.get.side_effect = ConnectionError("offline")
    failing_client.set.side_effect = ConnectionError("offline")
    app.state.cache = RedisCache(failing_client, ttl=60)

    car_id = create_car()
    assert client.get(f"{BASE}/{car_id}").status_code == status.HTTP_200_OK
    assert client.get(BASE).status_code == status.HTTP_200_OK
    assert (
        client.patch(f"{BASE}/{car_id}", json={"name": "Updated"}).status_code
        == status.HTTP_204_NO_CONTENT
    )
    assert client.delete(f"{BASE}/{car_id}").status_code == status.HTTP_204_NO_CONTENT


def test_read_and_fill_failures_are_best_effort():
    failing_client = Mock()
    failing_client.get.side_effect = ConnectionError("offline")
    failing_client.pipeline.side_effect = ConnectionError("offline")
    cache = RedisCache(failing_client, ttl=60)
    assert cache.get("key") is None
    assert cache.set("key", "value", "token") is None


@pytest.mark.parametrize("item", [False, True])
def test_old_in_flight_read_cannot_repopulate_cache(engine, cache, redis_client, create_car, item):
    car_id = create_car()
    with Session(engine) as session:
        repository = SQLCarRepository(session)
        original_read = repository.get if item else repository.list

        def read_during_write(request):
            result = original_read(request)
            cache.invalidate(car_id)
            return result

        if item:
            repository.get = read_during_write
        else:
            repository.list = read_during_write
        service = CarService(repository, cache)
        service.get(car_id) if item else service.list(CarFilters())
        key = car_cache_key(car_id) if item else list_cache_key(CarFilters())
        assert not redis_client.exists(key)


def test_fill_committed_before_fence_is_removed(cache, redis_client):
    key = list_cache_key(CarFilters())
    token = cache.token()

    cache.set(key, '{"response":{"cars":[],"next_cursor":null},"end_id":null}', token)
    assert redis_client.exists(key)

    cache.invalidate(1)
    assert not redis_client.exists(key)


def test_watch_conflict_skips_cache_fill(cache, redis_client, monkeypatch):
    token = cache.token()
    original_pipeline = redis_client.pipeline

    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        monkeypatch.setattr(pipe, "execute", Mock(side_effect=WatchError()))
        return pipe

    monkeypatch.setattr(redis_client, "pipeline", pipeline)
    cache.set(car_cache_key(1), "value", token)
    assert not redis_client.exists(car_cache_key(1))


@pytest.mark.parametrize("value", ["not json", "[]", '{"end_id":"invalid"}'])
def test_corrupt_page_cannot_break_invalidation(cache, redis_client, value):
    key = list_cache_key(CarFilters())
    redis_client.set(key, value)
    cache.invalidate(1)
    assert not redis_client.exists(key)


def test_expired_page_during_invalidation(cache, redis_client, monkeypatch):
    key = list_cache_key(CarFilters())
    redis_client.set(key, '{"end_id":null}')
    original_scan = redis_client.scan_iter

    def scan(*args, **kwargs):
        keys = list(original_scan(*args, **kwargs))
        redis_client.delete(key)
        yield from keys

    monkeypatch.setattr(redis_client, "scan_iter", scan)
    cache.invalidate(1)
    assert not redis_client.exists(key)


def test_evicted_token_does_not_allow_old_fill(cache, redis_client):
    old = cache.token()

    redis_client.delete(RedisCache.WRITE_TOKEN_KEY)
    assert cache.token() != old

    cache.set(car_cache_key(1), "old", old)
    assert not redis_client.exists(car_cache_key(1))


def test_missing_or_failed_write_does_not_invalidate_cache(
    cache, client, create_car, redis_client, engine
):
    create_car()
    client.get(BASE)
    key = list_cache_key(CarFilters())
    cached = redis_client.get(key)
    assert (
        client.patch(f"{BASE}/999", json={"name": "Missing"}).status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert redis_client.get(key) == cached

    with Session(engine) as session:
        repository = SQLCarRepository(session)
        repository.update = Mock(side_effect=RuntimeError("DB write failed"))
        service = CarService(repository, cache)
        with pytest.raises(RuntimeError):
            service.update(1, CarUpdate(name="New"))
    assert redis_client.get(key) == cached


def test_bulk_invalidation_preserves_other_app_keys(client, create_car, cache, redis_client):
    car_id = create_car()
    client.get(BASE)
    client.get(f"{BASE}/{car_id}")
    redis_client.set("other-app:key", "keep")
    cache.invalidate()
    assert not redis_client.exists(car_cache_key(car_id))
    assert not redis_client.exists(list_cache_key(CarFilters()))
    assert redis_client.get("other-app:key") == "keep"
