from django.urls import path
from rest_framework.routers import SimpleRouter

from modules.shops.api.v1.views import LoginAV, LogoutAV, MeAV, MePhotoAV, PartyViewSet, ProductViewSet, RegisterAV

router = SimpleRouter(use_regex_path=False)
router.register("parties", PartyViewSet, basename="party")
router.register("products", ProductViewSet, basename="product")

urlpatterns = [
    path("auth/login/", LoginAV.as_view()),
    path("auth/register/", RegisterAV.as_view()),
    path("auth/logout/", LogoutAV.as_view()),
    path("me/", MeAV.as_view()),
    path("me/photo/", MePhotoAV.as_view()),
    *router.urls,
]
