class CarNotFound(Exception):
    def __init__(self, car_id: int):
        self.car_id = car_id
        super().__init__(f"car {car_id} not found")


class CarOperationNotFound(Exception):
    def __init__(self, car_id: int, operation_id: int):
        super().__init__(f"operation {operation_id} for car {car_id} not found")


class CarOutOfStock(Exception):
    def __init__(self, car_id: int):
        super().__init__(f"car {car_id} is out of stock")


class CarStockLimitReached(Exception):
    def __init__(self, car_id: int):
        super().__init__(f"stock limit reached for car {car_id}")
