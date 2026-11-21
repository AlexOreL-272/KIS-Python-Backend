from typing import Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import Car
from app.schemas import CarCreate, CarFilters, CarRead, CarUpdate


class CarRepository(Protocol):
    def list(self, filters: CarFilters) -> list[CarRead]: ...
    def get(self, car_id: int) -> CarRead | None: ...
    def create(self, payload: CarCreate) -> int: ...
    def update(self, car_id: int, payload: CarUpdate) -> bool: ...
    def delete(self, car_id: int) -> bool: ...


class SQLCarRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self, filters: CarFilters) -> list[CarRead]:
        query = select(Car).where(Car.id > filters.cursor).order_by(Car.id)

        if filters.price_from is not None:
            query = query.where(Car.price >= filters.price_from)

        if filters.price_to is not None:
            query = query.where(Car.price <= filters.price_to)

        rows = self.session.scalars(query.limit(filters.limit + 1))
        return [CarRead.model_validate(row) for row in rows]

    def get(self, car_id: int) -> CarRead | None:
        car = self.session.get(Car, car_id)
        return CarRead.model_validate(car) if car is not None else None

    def create(self, payload: CarCreate) -> int:
        car = Car(**payload.model_dump())
        self.session.add(car)
        self.session.commit()
        return car.id

    def update(self, car_id: int, payload: CarUpdate) -> bool:
        result = self.session.execute(
            update(Car).where(Car.id == car_id).values(**payload.model_dump(exclude_unset=True))
        )
        self.session.commit()
        return result.rowcount == 1

    def delete(self, car_id: int) -> bool:
        result = self.session.execute(delete(Car).where(Car.id == car_id))
        self.session.commit()
        return result.rowcount == 1
