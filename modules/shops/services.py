from rest_framework import status

from base.exceptions import DomainError
from modules.shops.models import Shop


class NoShopError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN


def shop_for(user) -> Shop:
    shop = Shop.objects.filter(owner=user).first()
    if shop is None:
        raise NoShopError("NO_SHOP", "This account has no shop yet.")
    return shop
