from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_car_service
from app.schemas import (
    CarCreate,
    CarCreated,
    CarFilters,
    CarID,
    CarList,
    CarOperationRead,
    CarResponse,
    CarUpdate,
    ErrorResponse,
    OperationID,
)
from app.services.cars import CarService

router = APIRouter(
    prefix="/api/v1/cars",
    tags=["Cars"],
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Car not found"},
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Stock unavailable or database conflict",
        },
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Validation error",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "Database unavailable",
        },
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


def operation_response(operation: CarOperationRead, response: Response) -> CarOperationRead:
    response.headers["Location"] = f"/api/v1/cars/{operation.car_id}/operations/{operation.id}"
    return operation


@router.post(
    "/{car_id}/purchases",
    response_model=CarOperationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Buy one car from stock",
)
def buy_car(car_id: CarID, response: Response, service: Service) -> CarOperationRead:
    return operation_response(service.buy(car_id), response)


@router.post(
    "/{car_id}/sales",
    response_model=CarOperationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Sell one car into stock",
)
def sell_car(car_id: CarID, response: Response, service: Service) -> CarOperationRead:
    return operation_response(service.sell(car_id), response)


@router.get("/{car_id}/operations/{operation_id}", response_model=CarOperationRead)
def get_car_operation(
    car_id: CarID, operation_id: OperationID, service: Service
) -> CarOperationRead:
    return service.get_operation(car_id, operation_id)
