from app.enums import CarOperationType
from app.errors import CarNotFound, CarOperationNotFound
from app.repositories.cars import CarRepository
from app.schemas import CarCreate, CarDetail, CarFilters, CarList, CarOperationRead, CarUpdate


class CarService:
    def __init__(self, repository: CarRepository):
        self.repository = repository

    def list(self, filters: CarFilters) -> CarList:
        rows = self.repository.list(filters)
        page = rows[: filters.limit]
        return CarList(
            cars=page,
            next_cursor=page[-1].id if len(rows) > filters.limit else None,
        )

    def get(self, car_id: int) -> CarDetail:
        car = self.repository.get(car_id)
        if car is None:
            raise CarNotFound(car_id)
        return car

    def create(self, payload: CarCreate) -> int:
        return self.repository.create(payload)

    def update(self, car_id: int, payload: CarUpdate) -> None:
        if not self.repository.update(car_id, payload):
            raise CarNotFound(car_id)

    def delete(self, car_id: int) -> None:
        if not self.repository.delete(car_id):
            raise CarNotFound(car_id)

    def buy(self, car_id: int) -> CarOperationRead:
        return self.repository.operate(car_id, CarOperationType.BUY)

    def sell(self, car_id: int) -> CarOperationRead:
        return self.repository.operate(car_id, CarOperationType.SELL)

    def get_operation(self, car_id: int, operation_id: int) -> CarOperationRead:
        operation = self.repository.get_operation(car_id, operation_id)
        if operation is None:
            raise CarOperationNotFound(car_id, operation_id)
        return operation
