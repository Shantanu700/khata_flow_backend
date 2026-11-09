from drf_spectacular.openapi import AutoSchema


class BaseSchema(AutoSchema):
    method_mapping = {
        "get": "get",
        "post": "post",
        "put": "put",
        "patch": "patch",
        "delete": "delete",
    }
