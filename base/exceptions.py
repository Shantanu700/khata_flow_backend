from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class DomainError(Exception):
    "A business-rule refusal; `message` is safe to show the user verbatim."

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def error_body(code: str, message: str, details: object = None) -> dict:
    error: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response(error_body(exc.code, exc.message), status=exc.status_code)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, exceptions.ValidationError):
        response.data = error_body("VALIDATION_ERROR", "Some fields are invalid.", exc.detail)
    elif isinstance(exc, Http404):
        response.data = error_body("NOT_FOUND", "Not found.")
    elif isinstance(exc, exceptions.APIException):
        response.data = error_body(str(exc.default_code).upper(), str(exc.detail))
    return response
