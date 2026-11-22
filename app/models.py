from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.enums import CarOperationType


class Base(DeclarativeBase):
    pass


class Car(Base):
    __tablename__ = "cars"
    __table_args__ = (
        CheckConstraint("price > 0", name="ck_cars_positive_price"),
        CheckConstraint("length(trim(name)) > 0", name="ck_cars_nonempty_name"),
        CheckConstraint("colour ~ '^#[0-9a-fA-F]{6}$'", name="ck_cars_colour").ddl_if(
            dialect="postgresql"
        ),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(1024))
    colour: Mapped[str] = mapped_column(String(7))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    build_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stock: Mapped["CarStock"] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class CarStock(Base):
    __tablename__ = "car_stock"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_car_stock_nonnegative"),
        {"schema": "core"},
    )

    car_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("core.cars.id", ondelete="CASCADE"),
        primary_key=True,
    )
    amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class CarOperationLog(Base):
    __tablename__ = "car_operation_log"
    __table_args__ = (
        CheckConstraint(
            "amount_before >= 0 and amount_after >= 0", name="ck_car_operation_log_nonnegative"
        ),
        Index("ix_car_operation_log_car_id_id", "car_id", "id"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True
    )
    # Historical ID: the operation survives deletion of the car.
    car_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    operation_type: Mapped[CarOperationType] = mapped_column(
        Enum(
            CarOperationType,
            name="car_operation_type",
            schema="core",
            values_callable=lambda values: [value.value for value in values],
            create_constraint=True,
        )
    )
    amount_before: Mapped[int] = mapped_column(Integer)
    amount_after: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
