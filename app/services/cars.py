import logging
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.cache import CAR_PREFIX, LIST_PREFIX, Cache
from app.enums import CarOperationType
from app.errors import CarNotFound, CarOperationNotFound
from app.repositories.cars import CarRepository
from app.schemas import (
    CarCreate,
    CarDetail,
    CarFilters,
    CarID,
    CarList,
    CarOperationRead,
    CarUpdate,
)

logger = logging.getLogger(__name__)
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


def list_cache_key(filters: CarFilters) -> str:
    return f"{LIST_PREFIX}:{filters.cursor}:{filters.limit}"


def car_cache_key(car_id: int) -> str:
    return f"{CAR_PREFIX}:{car_id}"


class CachedCarList(BaseModel):
    response: CarList
    end_id: CarID | None


class CarService:
    def __init__(self, repository: CarRepository, cache: Cache):
        self.repository = repository
        self.cache = cache

    def _read(
        self, key: str, model: type[ResponseModel], load: Callable[[], ResponseModel]
    ) -> ResponseModel:
        token = self.cache.token()
        if token is not None:
            cached = self.cache.get(key)
            if cached is not None:
                try:
                    cached_response = model.model_validate_json(cached)
                except ValidationError:
                    logger.warning("Invalid cache entry; reloading from PostgreSQL")
                else:
                    logger.debug("Cache hit: key=%s", key)
                    return cached_response

        logger.debug("Cache miss: key=%s", key)
        result = load()
        self.cache.set(key, result.model_dump_json(), token)
        return result

    def list(self, filters: CarFilters) -> CarList:
        def load() -> CachedCarList:
            rows = self.repository.list(filters)
            page = rows[: filters.limit]
            has_more = len(rows) > filters.limit
            return CachedCarList(
                response=CarList(
                    cars=page,
                    next_cursor=page[-1].id if has_more else None,
                ),
                end_id=rows[-1].id if has_more else None,
            )

        if filters.price_from is not None or filters.price_to is not None:
            return load().response
        return self._read(list_cache_key(filters), CachedCarList, load).response

    def get(self, car_id: int) -> CarDetail:
        def load() -> CarDetail:
            car = self.repository.get(car_id)
            if car is None:
                raise CarNotFound(car_id)
            return car

        return self._read(car_cache_key(car_id), CarDetail, load)

    def create(self, payload: CarCreate) -> int:
        car_id = self.repository.create(payload)
        self.cache.invalidate(car_id)
        return car_id

    def update(self, car_id: int, payload: CarUpdate) -> None:
        if not self.repository.update(car_id, payload):
            raise CarNotFound(car_id)
        self.cache.invalidate(car_id)

    def delete(self, car_id: int) -> None:
        if not self.repository.delete(car_id):
            raise CarNotFound(car_id)
        self.cache.invalidate(car_id)

    def _operate(self, car_id: int, operation_type: CarOperationType) -> CarOperationRead:
        operation = self.repository.operate(car_id, operation_type)
        self.cache.invalidate(car_id, lists=False)
        return operation

    def buy(self, car_id: int) -> CarOperationRead:
        return self._operate(car_id, CarOperationType.BUY)

    def sell(self, car_id: int) -> CarOperationRead:
        return self._operate(car_id, CarOperationType.SELL)

    def get_operation(self, car_id: int, operation_id: int) -> CarOperationRead:
        operation = self.repository.get_operation(car_id, operation_id)
        if operation is None:
            raise CarOperationNotFound(car_id, operation_id)
        return operation
