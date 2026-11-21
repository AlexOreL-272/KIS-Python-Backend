from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_car_service
from app.schemas import (
    CarCreate,
    CarCreated,
    CarFilters,
    CarID,
    CarList,
    CarResponse,
    CarUpdate,
    ErrorResponse,
)
from app.services.cars import CarService

router = APIRouter(
    prefix="/api/v1/cars",
    tags=["Cars"],
    responses={
        404: {"model": ErrorResponse, "description": "Car not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        503: {"model": ErrorResponse, "description": "Database unavailable"},
    },
)

Service = Annotated[CarService, Depends(get_car_service)]


@router.get("", response_model=CarList, summary="List cars with price filters and pagination")
def list_cars(filters: Annotated[CarFilters, Query()], service: Service) -> CarList:
    return service.list(filters)


@router.get("/{car_id}", response_model=CarResponse, summary="Get a car")
def get_car(car_id: CarID, service: Service) -> CarResponse:
    return CarResponse(car=service.get(car_id))


@router.post("", response_model=CarCreated, status_code=status.HTTP_201_CREATED)
def create_car(payload: CarCreate, response: Response, service: Service) -> CarCreated:
    car_id = service.create(payload)
    response.headers["Location"] = f"/api/v1/cars/{car_id}"
    return CarCreated(id=car_id)


@router.patch("/{car_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_car(car_id: CarID, payload: CarUpdate, service: Service) -> Response:
    service.update(car_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{car_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_car(car_id: CarID, service: Service) -> Response:
    service.delete(car_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
