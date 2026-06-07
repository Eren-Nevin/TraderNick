class DataProviderError(Exception):
    pass


class DataProviderHTTPError(DataProviderError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")
