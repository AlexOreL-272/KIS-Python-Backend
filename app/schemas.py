from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.enums import CarOperationType

MAX_ID = 9_223_372_036_854_775_807
MAX_STOCK = 2_147_483_647

StockAmount = Annotated[int, Field(ge=0, le=MAX_STOCK)]
CarID = Annotated[int, Field(gt=0, le=MAX_ID)]
OperationID = Annotated[int, Field(gt=0, le=MAX_ID)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
Colour = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Money = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2, allow_inf_nan=False)]
PriceBound = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2, allow_inf_nan=False)]


class CarCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    colour: Colour
    price: Money
    build_date: date


class CarUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    colour: Colour | None = None
    price: Money | None = None
    build_date: date | None = None

    @model_validator(mode="after")
    def require_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("provide at least one field to update")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("updated fields cannot be null; omit unchanged fields")
        return self


class CarRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: CarID
    name: Name
    colour: Colour
    price: Money
    build_date: date


class CarCreated(BaseModel):
    id: CarID


class CarDetail(CarRead):
    amount: StockAmount


class CarResponse(BaseModel):
    car: CarDetail


class CarOperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: OperationID
    car_id: CarID = Field(description="Historical car ID; the car may have been deleted.")
    operation_type: CarOperationType
    amount_before: StockAmount
    amount_after: StockAmount
    occurred_at: AwareDatetime


class CarList(BaseModel):
    cars: list[CarRead]
    next_cursor: CarID | None = None


class CarFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price_from: PriceBound | None = None
    price_to: PriceBound | None = None
    cursor: Annotated[int, Field(ge=0, le=MAX_ID)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 10

    @model_validator(mode="after")
    def ordered_prices(self) -> Self:
        if (
            self.price_from is not None
            and self.price_to is not None
            and self.price_from > self.price_to
        ):
            raise ValueError("price_from must be less than or equal to price_to")
        return self


class ErrorResponse(BaseModel):
    error: str
    details: list[str] = Field(default_factory=list)
