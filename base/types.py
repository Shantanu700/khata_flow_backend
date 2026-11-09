from typing import Literal, TypeAlias, TypedDict

from pydantic import BaseModel


class AuthenticationMethodTypes(TypedDict, total=False):
    get: bool
    post: bool
    put: bool
    patch: bool
    delete: bool


AuthenticationType: TypeAlias = bool | AuthenticationMethodTypes


class DynamicKeysType(TypedDict):
    name: str
    source: str
    type: Literal["int", "str", "float", "bool"]


class ErrorBodyType(BaseModel):
    code: str
    message: str
    details: dict | list | None = None


class ErrorResponseType(BaseModel):
    error: ErrorBodyType


class DefaultSuccessResponseType(BaseModel):
    msg: str
