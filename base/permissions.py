from typing import cast

from rest_framework.permissions import BasePermission
from rest_framework.request import Request


def requires_authentication(view, method: str) -> bool:
    "The view's `authentication` attribute: a bool, or a per-method/per-action dict defaulting to True."
    authentication = getattr(view, "authentication", True)
    return authentication if isinstance(authentication, bool) else authentication.get(method, True)


class APIAuthenticationPermission(BasePermission):
    message = "Authentication credentials were not provided."

    def has_permission(self, request: Request, view) -> bool:  # type: ignore[override]
        method = getattr(view, "action", None) or cast(str, request.method).lower()
        if not requires_authentication(view, method):
            return True
        return bool(request.user and request.user.is_authenticated)
