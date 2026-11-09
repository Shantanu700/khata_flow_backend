from rest_framework import status

STATUS_MAPPING = {
    "get": status.HTTP_200_OK,
    "post": status.HTTP_201_CREATED,
    "put": status.HTTP_200_OK,
    "patch": status.HTTP_200_OK,
    "delete": status.HTTP_200_OK,
    "list": status.HTTP_200_OK,
    "retrieve": status.HTTP_200_OK,
    "create": status.HTTP_201_CREATED,
    "update": status.HTTP_200_OK,
    "partial_update": status.HTTP_200_OK,
    "destroy": status.HTTP_204_NO_CONTENT,
}

FIELDS_MAPPING = {
    "int": "IntegerField",
    "str": "CharField",
    "float": "FloatField",
    "bool": "BooleanField",
}
