from app.errors import CarNotFound
from app.repositories.cars import CarRepository
from app.schemas import CarCreate, CarFilters, CarList, CarRead, CarUpdate


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

    def get(self, car_id: int) -> CarRead:
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
