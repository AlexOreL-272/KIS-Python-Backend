from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.repositories.cars import SQLCarRepository
from app.services.cars import CarService


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_car_service(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> CarService:
    return CarService(SQLCarRepository(session), request.app.state.cache)
