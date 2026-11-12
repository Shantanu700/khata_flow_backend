import functools
from typing import Any

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.settings import api_settings

from base.constants import STATUS_MAPPING
from base.permissions import requires_authentication
from base.types import ErrorResponseType


def _paginated_type(type: Any):
    "The count/next/previous/results envelope that PageNumberPagination returns, around `type`."
    return inline_serializer(
        name=f"Paginated{type.__class__.__name__}",
        fields={
            "count": drf_serializers.IntegerField(),
            "next": drf_serializers.URLField(allow_null=True),
            "previous": drf_serializers.URLField(allow_null=True),
            "results": type.__class__(many=True),
        },
    )


# Handlers that page by hand (not through a DRF list view) would otherwise leave these out of the schema.
PAGE_PARAMETERS = [OpenApiParameter("page", int), OpenApiParameter("page_size", int)]


def extend_response(type: Any | None = None, paginated: bool = False):
    "Document a handler's success body with the default status code for its method (base.constants)."

    def decorator(f):
        code = STATUS_MAPPING.get(getattr(f, "__name__", ""))
        if code is None:
            return f
        body = _paginated_type(type) if paginated else type
        return extend_schema(
            responses={code: OpenApiResponse(body, description="Indicates that operation was successful.")},
            parameters=PAGE_PARAMETERS if paginated else None,
        )(f)

    return decorator


def _error(description: str, code: str, message: str, details: Any = None) -> OpenApiResponse:
    example = {"error": {"code": code, "message": message, **({"details": details} if details else {})}}
    return OpenApiResponse(ErrorResponseType, description, [OpenApiExample(code, value=example)])


def own_handler(cls, name: str):
    "A copy of an inherited handler, so per-class schema changes never leak into the parent or a DRF mixin."
    inherited = getattr(cls, name)

    @functools.wraps(inherited)
    def handler(self, *args, **kwargs):
        return inherited(self, *args, **kwargs)

    handler.kwargs = dict(getattr(inherited, "kwargs", {}))
    return handler


def extend_base_schema(cls, handler):
    """Add the shared error responses (400, 409, and 401/403 when authenticated) to a handler's schema.
    Responses set directly with @extend_schema on the handler are kept."""
    method = handler.__name__
    extend_schema_params = {}
    responses = {
        status.HTTP_400_BAD_REQUEST: _error(
            "Request body or query parameters failed validation.",
            "VALIDATION_ERROR",
            "Some fields are invalid.",
            {"text": ["This field is required."]},
        ),
        status.HTTP_409_CONFLICT: _error(
            "A business rule refused the request; the message can be shown to the user.",
            "NEGATIVE_STOCK",
            "Only 4 Packet of Maggi in stock.",
        ),
    }
    if requires_authentication(cls, method):
        responses[status.HTTP_401_UNAUTHORIZED] = _error(
            "Missing or invalid token.", "NOT_AUTHENTICATED", "Authentication credentials were not provided."
        )
        responses[status.HTTP_403_FORBIDDEN] = _error(
            "The account has no shop yet.", "NO_SHOP", "This account has no shop yet."
        )
    else:
        extend_schema_params["auth"] = []

    # Mirrors drf_spectacular.utils.extend_schema's own lookup of the schema class to extend.
    base_schema = (
        getattr(handler, "schema", None)
        or getattr(handler, "kwargs", {}).get("schema", None)
        or api_settings.DEFAULT_SCHEMA_CLASS
    )

    class ExtendedSchema(base_schema):  # type: ignore[misc, valid-type]
        def get_response_serializers(self):
            _responses = super().get_response_serializers() or {}
            if not isinstance(_responses, dict):
                _responses = {STATUS_MAPPING.get(method, status.HTTP_200_OK): _responses}
            return {**responses, **_responses}

    if not hasattr(handler, "kwargs"):
        handler.kwargs = {}
    handler.kwargs["schema"] = ExtendedSchema
    return extend_schema(**extend_schema_params)(handler)
