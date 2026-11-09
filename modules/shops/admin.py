from django.contrib import admin

from modules.shops.models import Party, Product, Shop


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "preferred_language", "created_at")
    search_fields = ("name",)


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "shop", "monthly_salary", "is_active")
    list_filter = ("kind", "is_active", "shop")
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "low_stock_threshold", "unit_price", "shop", "is_active")
    list_filter = ("unit", "is_active", "shop")
    search_fields = ("name",)
