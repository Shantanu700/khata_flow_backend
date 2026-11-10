from django.conf import settings
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response

from base.views import BaseAV
from modules.i18n.catalogs import catalog, languages
from modules.shops.choices import SHOP_LANGUAGES

# The catalogs only change with a deploy (and while developing, with every edit).
CACHE = "no-cache" if settings.DEBUG else "public, max-age=3600"


class LanguagesAV(BaseAV):
    "The languages the app offers, for the language picker."

    authentication = False

    @extend_schema(
        responses={
            200: inline_serializer(
                "Language",
                {"code": serializers.CharField(), "name": serializers.CharField(), "native_name": serializers.CharField()},
                many=True,
            )
        }
    )
    def get(self, request):
        return Response(languages(), headers={"Cache-Control": CACHE})


class CatalogAV(BaseAV):
    "Every word on the screens in one language, by key."

    authentication = False

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request, code: str):
        if code not in SHOP_LANGUAGES:
            raise Http404
        return Response(catalog(code), headers={"Cache-Control": CACHE})
