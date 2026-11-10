from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/schema/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/v1/", include("modules.shops.api.v1.urls")),
    path("api/v1/", include("modules.ledger.api.v1.urls")),
    path("api/v1/voice/", include("modules.voice.api.v1.urls")),
    path("api/v1/i18n/", include("modules.i18n.api.v1.urls")),
]

# Media (recordings and reply audio) is never served directly: reply audio goes through
# /api/v1/voice/commands/<id>/audio/, which checks the owner.
