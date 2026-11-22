from datetime import date
from decimal import Decimal

from redis import Redis
from sqlalchemy import func, select

from app.cache import RedisCache
from app.config import Settings
from app.database import build_engine, build_session_factory
from app.models import Car, CarStock

DEMO_CARS = [
    ("Tesla", "#008000", 50000),
    ("Mercedes", "#FF0000", 10000),
    ("Audi", "#0000FF", 15000),
    ("Rover", "#00FF00", 10000),
    ("BMW", "#FF00FF", 15000),
    ("Mercedes", "#FF00FF", 10000),
    ("Audi", "#FFFF00", 15000),
    ("Rolls Royce", "#FF0000", 10000),
    ("Bentley", "#00FFFF", 15000),
    ("Jaguar", "#FF00FF", 10000),
    ("Porsche", "#FFFF00", 15000),
    ("Land Rover", "#FF0000", 10000),
    ("Aston Martin", "#00FFFF", 15000),
    ("Ferrari", "#FF00FF", 10000),
    ("Lamborghini", "#FFFF00", 15000),
    ("Bugatti", "#FF0000", 10000),
    ("Maserati", "#00FFFF", 15000),
    ("Lotus", "#FF00FF", 10000),
    ("Moskvitch", "#FFFF00", 15000),
    ("Renault", "#FF0000", 10000),
    ("McLaren", "#00FFFF", 15000),
    ("Alfa Romeo", "#FF00FF", 10000),
    ("Pagani", "#FFFF00", 15000),
    ("Lada", "#FF0000", 10000),
    ("Geely", "#995AAA", 15000),
]


def main() -> None:
    settings = Settings()
    engine = build_engine(settings)

    try:
        with build_session_factory(engine)() as session:
            if session.scalar(select(func.count()).select_from(Car)):
                print("Catalog already contains cars; no demo rows inserted.")
                return

            session.add_all(
                Car(
                    name=name,
                    colour=colour,
                    price=Decimal(price),
                    build_date=date(2022, 1, index),
                    stock=CarStock(amount=0),
                )
                for index, (name, colour, price) in enumerate(DEMO_CARS, start=1)
            )
            session.commit()

        with Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        ) as client:
            RedisCache(client, settings.cache_ttl_seconds).invalidate()
        print("Inserted 25 demo cars.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
