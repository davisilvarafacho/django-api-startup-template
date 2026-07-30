from rest_framework.exceptions import APIException


class APIError(APIException):
    def __init__(self, code: str, *, status_code: int, message: str | None = None, context: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.context = context or {}
        super().__init__({"code": code, "message": message or code})
