class CarNotFound(Exception):
    def __init__(self, car_id: int):
        self.car_id = car_id
        super().__init__(f"car {car_id} not found")
