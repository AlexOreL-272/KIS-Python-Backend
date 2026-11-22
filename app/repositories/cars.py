from typing import Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.enums import CarOperationType
from app.errors import CarNotFound, CarOutOfStock, CarStockLimitReached
from app.models import Car, CarOperationLog, CarStock
from app.schemas import (
    MAX_STOCK,
    CarCreate,
    CarDetail,
    CarFilters,
    CarOperationRead,
    CarRead,
    CarUpdate,
)


class CarRepository(Protocol):
    def list(self, filters: CarFilters) -> list[CarRead]: ...
    def get(self, car_id: int) -> CarDetail | None: ...
    def create(self, payload: CarCreate) -> int: ...
    def update(self, car_id: int, payload: CarUpdate) -> bool: ...
    def delete(self, car_id: int) -> bool: ...
    def operate(self, car_id: int, operation_type: CarOperationType) -> CarOperationRead: ...
    def get_operation(self, car_id: int, operation_id: int) -> CarOperationRead | None: ...


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

    def get(self, car_id: int) -> CarDetail | None:
        row = self.session.execute(
            select(Car, CarStock.amount).join(CarStock).where(Car.id == car_id)
        ).one_or_none()

        if row is None:
            return None

        return CarDetail(**CarRead.model_validate(row.Car).model_dump(), amount=row.amount)

    def create(self, payload: CarCreate) -> int:
        car = Car(**payload.model_dump(), stock=CarStock(amount=1))
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

    def operate(self, car_id: int, operation_type: CarOperationType) -> CarOperationRead:
        with self.session.begin():
            car = self.session.scalar(select(Car.id).where(Car.id == car_id).with_for_update())
            if car is None:
                raise CarNotFound(car_id)

            is_buy = operation_type == CarOperationType.BUY
            query = update(CarStock).where(CarStock.car_id == car_id)
            query = query.where(CarStock.amount > 0 if is_buy else CarStock.amount < MAX_STOCK)

            amount = self.session.scalar(
                query.values(amount=CarStock.amount + (-1 if is_buy else 1)).returning(
                    CarStock.amount
                )
            )

            if amount is None:
                if is_buy:
                    raise CarOutOfStock(car_id)
                raise CarStockLimitReached(car_id)

            operation = self.session.scalars(
                select(CarOperationLog)
                .where(CarOperationLog.car_id == car_id)
                .order_by(CarOperationLog.id.desc())
                .limit(1)
            ).one()

            return CarOperationRead.model_validate(operation)

    def get_operation(self, car_id: int, operation_id: int) -> CarOperationRead | None:
        operation = self.session.scalar(
            select(CarOperationLog).where(
                CarOperationLog.id == operation_id, CarOperationLog.car_id == car_id
            )
        )

        return CarOperationRead.model_validate(operation) if operation is not None else None
