from django.urls import path
from rest_framework.routers import SimpleRouter

from modules.ledger.api.v1.views import EntryViewSet, SummaryAV

router = SimpleRouter(use_regex_path=False)
router.register("entries", EntryViewSet, basename="entry")

urlpatterns = [path("summary/", SummaryAV.as_view()), *router.urls]
