class DomainError(Exception):
    def __init__(self, message: str, status: int = 422, **details):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details: dict = details
