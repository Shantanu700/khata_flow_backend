import calendar
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from base.exceptions import DomainError
from modules.ledger.choices import CUSTOMER_MONEY_KINDS, STAFF_MONEY_KINDS, STOCK_KINDS, EntryKind
from modules.ledger.models import Entry
from modules.shops.choices import PartyKind
from modules.shops.models import Party, Product, Shop

ZERO = Decimal("0")
MONEY = DecimalField(max_digits=14, decimal_places=3)


class LedgerError(DomainError):
    pass


@dataclass(frozen=True)
class StaffDue:
    days_present: int
    days_in_month: int
    earned: Decimal
    advances: Decimal
    salary_paid: Decimal
    due: Decimal


def live_entries() -> QuerySet[Entry]:
    "Entries that count: not reversed, and not reversals themselves."
    return Entry.objects.filter(reversal__isnull=True).exclude(kind=EntryKind.REVERSAL)


def _live_sum(field: str, kind: EntryKind, prefix: str = "entries__") -> Coalesce:
    live = Q(**{f"{prefix}reversal__isnull": True, f"{prefix}kind": kind})
    return Coalesce(
        Sum(f"{prefix}{field}", filter=live),
        Value(ZERO),
        output_field=MONEY,
    )


def with_balance(parties: QuerySet[Party]) -> QuerySet[Party]:
    return parties.annotate(
        balance=_live_sum("amount", EntryKind.CREDIT_GIVEN) - _live_sum("amount", EntryKind.PAYMENT_RECEIVED)
    )


def with_stock_level(products: QuerySet[Product]) -> QuerySet[Product]:
    return products.annotate(
        stock_level=_live_sum("quantity", EntryKind.STOCK_IN) - _live_sum("quantity", EntryKind.STOCK_OUT)
    )


def customer_balance(party: Party) -> Decimal:
    return with_balance(Party.objects.filter(pk=party.pk)).get().balance


def total_outstanding(shop: Shop) -> Decimal:
    debtors = with_balance(shop.parties.filter(kind=PartyKind.CUSTOMER)).filter(balance__gt=0)
    return sum((p.balance for p in debtors), ZERO)


def top_debtors(shop: Shop, limit: int = 5) -> list[Party]:
    customers = with_balance(shop.parties.filter(kind=PartyKind.CUSTOMER))
    return list(customers.filter(balance__gt=0).order_by("-balance", "name")[:limit])


def stock_level(product: Product) -> Decimal:
    return with_stock_level(Product.objects.filter(pk=product.pk)).get().stock_level


def low_stock_products(shop: Shop) -> list[Product]:
    products = with_stock_level(shop.products.filter(is_active=True))
    return list(products.filter(stock_level__lte=F("low_stock_threshold")).order_by("name"))


def staff_due(party: Party, month: date) -> StaffDue:
    days_in_month = calendar.monthrange(month.year, month.month)[1]
    entries = live_entries().filter(
        party=party,
        occurred_on__gte=month.replace(day=1),
        occurred_on__lte=month.replace(day=days_in_month),
    )
    totals = entries.aggregate(
        days_present=Count("occurred_on", filter=Q(kind=EntryKind.ATTENDANCE), distinct=True),
        advances=Coalesce(Sum("amount", filter=Q(kind=EntryKind.STAFF_ADVANCE)), Value(ZERO), output_field=MONEY),
        salary_paid=Coalesce(Sum("amount", filter=Q(kind=EntryKind.SALARY_PAID)), Value(ZERO), output_field=MONEY),
    )
    salary = party.monthly_salary or ZERO
    earned = (salary * totals["days_present"] / days_in_month).quantize(Decimal("1"), ROUND_HALF_UP)
    return StaffDue(
        days_present=totals["days_present"],
        days_in_month=days_in_month,
        earned=earned,
        advances=totals["advances"],
        salary_paid=totals["salary_paid"],
        due=earned - totals["advances"] - totals["salary_paid"],
    )


def month_bounds(month: date) -> tuple[date, date]:
    return month.replace(day=1), month.replace(day=calendar.monthrange(month.year, month.month)[1])


def attendance_entry_id(party: Party, on: date) -> int | None:
    return live_entries().filter(kind=EntryKind.ATTENDANCE, party=party, occurred_on=on).values_list("id", flat=True).first()


def present_days(party: Party, start: date, end: date) -> list[date]:
    days = live_entries().filter(kind=EntryKind.ATTENDANCE, party=party, occurred_on__range=(start, end))
    return sorted(set(days.values_list("occurred_on", flat=True)))


def with_month_advances(parties: QuerySet[Party], month: date) -> QuerySet[Party]:
    start, end = month_bounds(month)
    live = Q(
        entries__reversal__isnull=True,
        entries__kind=EntryKind.STAFF_ADVANCE,
        entries__occurred_on__range=(start, end),
    )
    return parties.annotate(month_advances=Coalesce(Sum("entries__amount", filter=live), Value(ZERO), output_field=MONEY))


def salary_left(party: Party, month: date) -> Decimal:
    "What can still be handed over this month as advance or salary: the monthly salary less both."
    due = staff_due(party, month)
    return (party.monthly_salary or ZERO) - due.advances - due.salary_paid


def _validate(
    shop: Shop,
    kind: EntryKind,
    party: Party | None,
    product: Product | None,
    amount: Decimal | None,
    quantity: Decimal | None,
) -> None:
    if party is not None and party.shop_id != shop.pk or product is not None and product.shop_id != shop.pk:
        raise LedgerError("WRONG_SHOP", "That party or product belongs to another shop.")
    if kind in CUSTOMER_MONEY_KINDS + STAFF_MONEY_KINDS and (amount is None or amount <= 0):
        raise LedgerError("INVALID_AMOUNT", "Amount must be more than zero.")
    if kind in STOCK_KINDS and (product is None or quantity is None or quantity <= 0):
        raise LedgerError("INVALID_QUANTITY", "Stock entries need a product and a quantity above zero.")
    expected = {
        **dict.fromkeys(CUSTOMER_MONEY_KINDS, PartyKind.CUSTOMER),
        **dict.fromkeys(STAFF_MONEY_KINDS + (EntryKind.ATTENDANCE,), PartyKind.STAFF),
    }.get(kind)
    if expected and (party is None or party.kind != expected):
        raise LedgerError("WRONG_PARTY_KIND", f"{EntryKind(kind).label} needs a {expected.label.lower()}.")
    if kind == EntryKind.REVERSAL:
        raise LedgerError("INVALID_ENTRY", "Use reverse_entry() to undo an entry.")


def _ensure_stock_covers(product: Product, quantity: Decimal) -> None:
    Product.objects.select_for_update().get(pk=product.pk)
    available = stock_level(product)
    if available - quantity < 0:
        raise LedgerError("NEGATIVE_STOCK", f"Only {available.normalize():f} {product.get_unit_display()} of {product.name} in stock.")


def record_entry(
    shop: Shop,
    kind: EntryKind,
    *,
    party: Party | None = None,
    product: Product | None = None,
    amount: Decimal | None = None,
    quantity: Decimal | None = None,
    occurred_on: date | None = None,
    note: str = "",
    voice_command=None,
) -> Entry:
    _validate(shop, kind, party, product, amount, quantity)
    occurred_on = occurred_on or timezone.localdate()
    with transaction.atomic():
        if kind == EntryKind.ATTENDANCE:
            Party.objects.select_for_update().get(pk=party.pk)
            existing = live_entries().filter(kind=kind, party=party, occurred_on=occurred_on).first()
            if existing:
                return existing
        if kind == EntryKind.STOCK_OUT:
            _ensure_stock_covers(product, quantity)
        return Entry.objects.create(
            shop=shop,
            kind=kind,
            party=party,
            product=product,
            amount=amount,
            quantity=quantity,
            occurred_on=occurred_on,
            note=note,
            voice_command=voice_command,
        )


def reverse_entry(entry: Entry, voice_command=None) -> Entry:
    if entry.kind == EntryKind.REVERSAL:
        raise LedgerError("CANNOT_REVERSE_REVERSAL", "A reversal cannot itself be undone.")
    with transaction.atomic():
        entry = Entry.objects.select_for_update().get(pk=entry.pk)
        if Entry.objects.filter(reverses=entry).exists():
            raise LedgerError("ALREADY_REVERSED", "That entry was already undone.")
        if entry.kind == EntryKind.STOCK_IN:
            _ensure_stock_covers(entry.product, entry.quantity)
        return Entry.objects.create(
            shop_id=entry.shop_id,
            kind=EntryKind.REVERSAL,
            reverses=entry,
            party_id=entry.party_id,
            product_id=entry.product_id,
            amount=entry.amount,
            quantity=entry.quantity,
            voice_command=voice_command,
        )


def undo_last(shop: Shop, voice_command=None) -> Entry:
    with transaction.atomic():
        Shop.objects.select_for_update().get(pk=shop.pk)
        target = live_entries().filter(shop=shop).order_by("-id").first()
        if target is None:
            raise LedgerError("NOTHING_TO_UNDO", "There is no entry to undo.")
        return reverse_entry(target, voice_command=voice_command)


def format_inr(amount: Decimal | int) -> str:
    "Indian grouping: ₹1,25,000 and ₹1,250.50; paise only when non-zero."
    value = Decimal(amount).quantize(Decimal("0.01"), ROUND_HALF_UP)
    rupees, paise = divmod(abs(value) * 100, 100)
    grouped = re.sub(r"(\d)(?=(\d{2})*\d{3}$)", r"\1,", str(int(rupees)))
    sign = "-" if value < 0 else ""
    return f"{sign}₹{grouped}" + (f".{int(paise):02d}" if paise else "")
