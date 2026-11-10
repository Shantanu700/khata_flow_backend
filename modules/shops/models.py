from django.conf import settings
from django.db import models
from django.db.models import Q

from base.enums import ACTIVE_STATUSES
from base.model import ModelBase
from modules.shops.choices import PartyKind, Unit

ACTIVE = Q(status__in=ACTIVE_STATUSES)


class Shop(ModelBase):
    # One owner per shop, so request.user.shop scopes every query.
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shop")
    name = models.CharField(max_length=200)
    preferred_language = models.CharField(max_length=16, default="en-IN")
    photo = models.FileField(upload_to="photos/%Y/%m/", null=True, blank=True)

    def __str__(self) -> str:
        return self.name


class Party(ModelBase):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="parties")
    name = models.CharField(max_length=200)
    aliases = models.JSONField(default=list, blank=True)
    kind = models.CharField(max_length=16, choices=PartyKind.choices, default=PartyKind.CUSTOMER)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    photo = models.FileField(upload_to="photos/%Y/%m/", null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "parties"
        constraints = [
            models.UniqueConstraint(fields=["shop", "name"], condition=ACTIVE, name="uniq_party_shop_name")
        ]

    def __str__(self) -> str:
        return self.name


class Product(ModelBase):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=200)
    aliases = models.JSONField(default=list, blank=True)
    unit = models.CharField(max_length=16, choices=Unit.choices, default=Unit.PACKET)
    low_stock_threshold = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shop", "name"], condition=ACTIVE, name="uniq_product_shop_name")
        ]

    def __str__(self) -> str:
        return self.name
