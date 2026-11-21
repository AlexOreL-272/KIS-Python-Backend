from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Identity,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
