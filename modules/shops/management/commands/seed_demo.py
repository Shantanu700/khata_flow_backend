from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from modules.ledger.choices import EntryKind
from modules.ledger.services import record_entry
from modules.shops.choices import PartyKind, Unit
from modules.shops.models import Party, Product, Shop

C, P, A = EntryKind.CREDIT_GIVEN, EntryKind.PAYMENT_RECEIVED, EntryKind.STAFF_ADVANCE
IN, OUT = EntryKind.STOCK_IN, EntryKind.STOCK_OUT

# Two synthetic merchants with separate logins, so shop isolation can be demoed. No phone numbers or other
# contact details on purpose. History rows are (days ago, kind, party or product, amount or quantity).
MERCHANTS = [
    {
        "username": "gupta_store",
        "shop": "Gupta General Store",
        "customers": {
            "Sharma ji": ["sharma", "sharma uncle"],
            "Verma ji": ["verma", "varma"],
            "Gupta aunty": ["gupta", "gupta ji"],
            "Mishra ji": ["mishra", "mishra uncle"],
        },
        "staff": {"Ramu": ("9000", ["ramu", "ramu bhaiya"]), "Shyam": ("8000", ["shyam", "shyaam"])},
        "products": {
            "Aashirvaad Aata 5kg": (Unit.PACKET, 5, "285", ["aata", "atta", "aashirvaad", "ashirwad"]),
            "Amul Butter 100g": (Unit.PACKET, 5, "58", ["butter", "amul butter", "makkhan"]),
            "Maggi": (Unit.PACKET, 12, "14", ["maggie", "noodles"]),
            "Tata Salt": (Unit.PACKET, 8, "28", ["namak", "tata namak", "salt"]),
            "Fortune Oil 1L": (Unit.PACKET, 4, "165", ["tel", "oil", "fortune", "fortune tel"]),
            "Parle-G": (Unit.PACKET, 15, "10", ["parle", "parle g", "biscuit"]),
        },
        "history": [
            (14, IN, "Aashirvaad Aata 5kg", 20), (14, IN, "Amul Butter 100g", 15), (14, IN, "Maggi", 60),
            (14, IN, "Tata Salt", 25), (14, IN, "Fortune Oil 1L", 12), (14, IN, "Parle-G", 50),
            (13, C, "Sharma ji", 500), (12, C, "Verma ji", 1200), (12, OUT, "Maggi", 20),
            (11, C, "Gupta aunty", 400), (11, C, "Mishra ji", 2000), (10, OUT, "Aashirvaad Aata 5kg", 7),
            (9, C, "Sharma ji", 750), (9, OUT, "Parle-G", 18), (8, P, "Verma ji", 500), (8, OUT, "Tata Salt", 10),
            (7, OUT, "Amul Butter 100g", 6), (6, C, "Gupta aunty", 350), (6, A, "Ramu", 1000),
            (5, P, "Mishra ji", 2000), (5, OUT, "Maggi", 30), (4, OUT, "Fortune Oil 1L", 6), (4, A, "Shyam", 500),
            (3, C, "Sharma ji", 300), (3, OUT, "Tata Salt", 8), (2, P, "Gupta aunty", 200),
            (2, OUT, "Amul Butter 100g", 5), (1, P, "Sharma ji", 300), (1, OUT, "Aashirvaad Aata 5kg", 5),
            (1, OUT, "Parle-G", 12),
        ],
        "attendance": {"Ramu": [8, 7, 6, 5, 4, 3, 2, 1], "Shyam": [8, 7, 5, 4, 2, 1]},
    },
    {
        "username": "patel_store",
        "shop": "Patel Provision Store",
        "customers": {
            "Rathore ji": ["rathore", "rathod"],
            "Khan sahab": ["khan", "khan bhai"],
            "Joshi ji": ["joshi"],
            "Kapoor aunty": ["kapoor"],
        },
        "staff": {"Raju": ("8500", ["raju", "raju bhai"]), "Mohan": ("7500", ["mohan"])},
        "products": {
            "Tata Tea 250g": (Unit.PACKET, 6, "135", ["chai", "chai patti", "tata tea"]),
            "Basmati Rice 5kg": (Unit.PACKET, 3, "520", ["chawal", "rice", "basmati"]),
            "Toor Dal 1kg": (Unit.PACKET, 5, "165", ["dal", "arhar dal", "toor dal"]),
            "Surf Excel 1kg": (Unit.PACKET, 4, "140", ["surf", "sarf", "washing powder"]),
            "Lifebuoy Soap": (Unit.PIECE, 10, "38", ["sabun", "lifebuoy"]),
            "Amul Milk 500ml": (Unit.PACKET, 10, "34", ["doodh", "milk", "amul doodh"]),
        },
        "history": [
            (14, IN, "Tata Tea 250g", 20), (14, IN, "Basmati Rice 5kg", 10), (14, IN, "Toor Dal 1kg", 15),
            (14, IN, "Surf Excel 1kg", 12), (14, IN, "Lifebuoy Soap", 40), (14, IN, "Amul Milk 500ml", 30),
            (12, C, "Rathore ji", 800), (11, C, "Khan sahab", 1500), (10, OUT, "Basmati Rice 5kg", 8),
            (9, C, "Joshi ji", 450), (8, P, "Khan sahab", 1000), (7, OUT, "Amul Milk 500ml", 24),
            (6, A, "Raju", 700), (5, C, "Kapoor aunty", 600), (5, OUT, "Tata Tea 250g", 9),
            (4, OUT, "Lifebuoy Soap", 18), (3, P, "Rathore ji", 300), (3, OUT, "Toor Dal 1kg", 6),
            (2, C, "Rathore ji", 250), (2, OUT, "Surf Excel 1kg", 9), (1, P, "Joshi ji", 450),
        ],
        "attendance": {"Raju": [8, 7, 6, 4, 3, 2, 1], "Mohan": [8, 6, 5, 3, 2, 1]},
    },
]


class Command(BaseCommand):
    help = "Create two synthetic demo merchants (Gupta General Store, Patel Provision Store). Safe to rerun."

    @transaction.atomic
    def handle(self, *args, **options):
        for merchant in MERCHANTS:
            self._merchant(merchant)

    def _merchant(self, merchant: dict) -> None:
        user, created = get_user_model().objects.get_or_create(username=merchant["username"])
        if created:
            user.set_unusable_password()
            user.save()
        token, token_created = Token.objects.get_or_create(user=user)
        shop, _ = Shop.objects.get_or_create(owner=user, defaults={"name": merchant["shop"], "preferred_language": "hi-IN"})

        for name, aliases in merchant["customers"].items():
            Party.objects.get_or_create(shop=shop, name=name, defaults={"aliases": aliases})
        for name, (salary, aliases) in merchant["staff"].items():
            Party.objects.get_or_create(
                shop=shop,
                name=name,
                defaults={"kind": PartyKind.STAFF, "monthly_salary": Decimal(salary), "aliases": aliases},
            )
        for name, (unit, threshold, price, aliases) in merchant["products"].items():
            Product.objects.get_or_create(
                shop=shop,
                name=name,
                defaults={"unit": unit, "low_stock_threshold": threshold, "unit_price": Decimal(price), "aliases": aliases},
            )

        label = f"{merchant['username']} ({shop.name})"
        if shop.entries.exists():
            self.stdout.write(f"{label}: entries already exist; left untouched.")
        else:
            self._history(shop, merchant)
            self.stdout.write(f"{label}: created {shop.entries.count()} entries.")
        if token_created:
            self.stdout.write(self.style.WARNING(f"{label} API token (shown once): {token.key}"))
        else:
            self.stdout.write(f"{label}: token already exists. Show it with: manage.py drf_create_token {user.username}")

    def _history(self, shop: Shop, merchant: dict) -> None:
        today = timezone.localdate()
        parties = {p.name: p for p in shop.parties.all()}
        products = {p.name: p for p in shop.products.all()}
        for days_ago, kind, name, value in merchant["history"]:
            on = today - timedelta(days_ago)
            if kind in (IN, OUT):
                record_entry(shop, kind, product=products[name], quantity=Decimal(value), occurred_on=on)
            else:
                record_entry(shop, kind, party=parties[name], amount=Decimal(value), occurred_on=on)
        for name, days in merchant["attendance"].items():
            for days_ago in days:
                record_entry(shop, EntryKind.ATTENDANCE, party=parties[name], occurred_on=today - timedelta(days_ago))
