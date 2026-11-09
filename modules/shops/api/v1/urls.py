from rest_framework.routers import SimpleRouter

from modules.shops.api.v1.views import PartyViewSet, ProductViewSet

router = SimpleRouter(use_regex_path=False)
router.register("parties", PartyViewSet, basename="party")
router.register("products", ProductViewSet, basename="product")

urlpatterns = router.urls
