from django.urls import path

from modules.i18n.api.v1.views import CatalogAV, LanguagesAV

urlpatterns = [
    path("", LanguagesAV.as_view()),
    path("<str:code>/", CatalogAV.as_view()),
]
