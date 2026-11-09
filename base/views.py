from typing import cast

from pydantic import BaseModel
from rest_framework import status, viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from base.constants import STATUS_MAPPING
from base.decorators import extend_base_schema, own_handler
from base.exceptions import DomainError
from base.permissions import APIAuthenticationPermission
from base.types import AuthenticationType

VIEWSET_ACTIONS = ("list", "create", "retrieve", "update", "partial_update", "destroy")


class BaseAV(APIView):
    """APIView whose handlers may return a dict, list, str or pydantic model (wrapped in a Response with the
    status from base.constants.STATUS_MAPPING), and whose OpenAPI schema gets the shared error responses.
    `authentication` (bool or per-method dict) is the single switch for whether a handler needs a token."""

    permission_classes = [APIAuthenticationPermission]
    authentication: AuthenticationType = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for method in cls.http_method_names:
            if callable(getattr(cls, method, None)) and method != "options":
                setattr(cls, method, extend_base_schema(cls, own_handler(cls, method)))

    def finalize_response(self, request: Request, response, *args, **kwargs):
        if isinstance(response, (list, tuple, dict, str, BaseModel)):
            if isinstance(response, BaseModel):
                response = response.model_dump(mode="json")
            code = STATUS_MAPPING.get(cast(str, request.method).lower(), status.HTTP_200_OK)
            response = Response(response, status=code)
        return super().finalize_response(request, response, *args, **kwargs)

    def fail(self, code: str, message: str):
        "Refuse with a 409; `message` is shown to the user."
        raise DomainError(code, message)

    def success(self, message: str) -> dict:
        return {"msg": message}


class BaseViewSet(viewsets.GenericViewSet):
    "GenericViewSet with BaseAV's permission and schema conventions for its actions."

    permission_classes = [APIAuthenticationPermission]
    authentication: AuthenticationType = True
    # Typed <int:pk> routes (with SimpleRouter(use_regex_path=False)), so the schema knows ids are integers.
    lookup_value_converter = "int"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        names = [name for name in VIEWSET_ACTIONS if callable(getattr(cls, name, None))]
        names += [action.__name__ for action in cls.get_extra_actions()]
        for name in names:
            setattr(cls, name, extend_base_schema(cls, own_handler(cls, name)))
