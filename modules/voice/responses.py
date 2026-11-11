import json
import re
from contextvars import ContextVar
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

from django.utils import timezone

from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry
from modules.ledger.services import StaffDue, format_inr
from modules.shops.models import Party, Product

DIR = Path(__file__).parent / "replies"
# hi-Latn (Hinglish, the original wording) is the source: every other catalog has exactly its keys.
BASE = "hi-Latn"
COUNT_WORDS = ["", "Ek", "Do", "Teen", "Chaar", "Paanch", "Chhah", "Saat", "Aath", "Nau", "Das"]

# The command being answered sets this, so every reply below comes in its language.
language: ContextVar[str] = ContextVar("reply_language", default=BASE)


@cache
def catalog(code: str) -> dict[str, str]:
    base = json.loads((DIR / f"{BASE}.json").read_text("utf-8"))
    path = DIR / f"{code}.json"
    return {**base, **json.loads(path.read_text("utf-8"))} if code != BASE and path.exists() else base


def say(key: str, **values) -> str:
    return catalog(language.get())[key].format(**values)


def fallback() -> str:
    return say("fallback")


def spoken_error(code: str) -> str:
    key = f"error.{code}"
    return say(key) if key in catalog(language.get()) else fallback()


def yes_no() -> tuple[str, str]:
    return say("yes"), say("no")


def qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def unit(product: Product) -> str:
    return say(f"unit.{product.unit}")


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {say('and')} {names[-1]}"


def count_word(n: int) -> str:
    if language.get() == BASE and n < len(COUNT_WORDS):
        return COUNT_WORDS[n]
    return str(n)


def balance_line(balance: Decimal) -> str:
    if balance > 0:
        return say("balance.owing", amount=format_inr(balance))
    if balance < 0:
        return say("balance.advance", amount=format_inr(-balance))
    return say("balance.clear")


def credit_added(party: Party, amount: Decimal, balance: Decimal) -> str:
    return say("credit_added", name=party.name, amount=format_inr(amount), balance=balance_line(balance))


def payment_received(party: Party, amount: Decimal, balance: Decimal) -> str:
    return say("payment_received", name=party.name, amount=format_inr(amount), balance=balance_line(balance))


def stock_changed(entry: Entry, level: Decimal) -> str:
    key = "stock_in" if entry.kind == EntryKind.STOCK_IN else "stock_out"
    product = entry.product
    return say(key, name=product.name, qty=qty(entry.quantity), unit=unit(product), level=qty(level))


def attendance_marked(party: Party, already: bool) -> str:
    return say("attendance_already" if already else "attendance_marked", name=party.name)


def staff_paid(entry: Entry, due: StaffDue) -> str:
    key = "advance_paid" if entry.kind == EntryKind.STAFF_ADVANCE else "salary_paid"
    return say(key, name=entry.party.name, amount=format_inr(entry.amount), due=format_inr(due.due))


def customer_balance(party: Party, balance: Decimal) -> str:
    if balance > 0:
        return say("customer.owes", name=party.name, amount=format_inr(balance))
    if balance < 0:
        return say("customer.advance", name=party.name, amount=format_inr(-balance))
    return say("customer.clear", name=party.name)


def total_outstanding(total: Decimal, debtors: int) -> str:
    if total <= 0:
        return say("total.none")
    return say("total.some", amount=format_inr(total), count=debtors)


def low_stock(products: list[Product]) -> str:
    if not products:
        return say("low.none")
    names = join_names([p.name for p in products])
    if len(products) == 1:
        return say("low.one", names=names)
    return say("low.many", count=count_word(len(products)), names=names)


def stock_level(product: Product, level: Decimal) -> str:
    return say("stock_level", name=product.name, qty=qty(level), unit=unit(product))


def month_label(month: date) -> str:
    today = timezone.localdate()
    back = (today.year - month.year) * 12 + today.month - month.month
    if back <= 0:
        return say("when.this_month")
    return say("when.last_month") if back == 1 else say("when.months_ago", months=back)


def staff_due(party: Party, due: StaffDue, month: date) -> str:
    parts = [say("staff.earned", amount=format_inr(due.earned))]
    if due.advances:
        parts.append(say("staff.advances", amount=format_inr(due.advances)))
    if due.salary_paid:
        parts.append(say("staff.paid", amount=format_inr(due.salary_paid)))
    if not due.advances and not due.salary_paid:
        parts.append(say("staff.none_paid"))
    owed = say("staff.left", amount=format_inr(due.due)) if due.due >= 0 else say("staff.over", amount=format_inr(-due.due))
    return f"{say('staff.head', name=party.name, days=f'{float(due.days_present):g}', when=month_label(month))} {', '.join(parts)}, {owed}."


def staff_list(staff: list[Party]) -> str:
    if not staff:
        return say("staff_list.none")
    names = join_names([p.name for p in staff])
    return say("staff_list.one", names=names) if len(staff) == 1 else say("staff_list.many", count=len(staff), names=names)


def attendance_today(staff: list[Party], present: list[Party]) -> str:
    if not staff:
        return say("staff_list.none")
    if not present:
        return say("attendance.none")
    if len(present) == len(staff):
        return say("attendance.all", names=join_names([p.name for p in staff]))
    absent = [p for p in staff if p not in present]
    return say("attendance.some", present=join_names([p.name for p in present]), absent=join_names([p.name for p in absent]))


def customer_count(customers: int, owing: int) -> str:
    if not customers:
        return say("customers.none")
    return say("customers.owing" if owing else "customers.clear", count=customers, owing=owing)


def _list(key: str, items: list[str], cap: int) -> str:
    "Speaks the first `cap` items and only counts the rest, so a long list stays short to listen to."
    text = say(key, count=len(items), items=join_names(items[:cap]))
    return f"{text} {say('more', count=len(items) - cap)}" if len(items) > cap else text


def debtor_list(debtors: list[Party]) -> str:
    if not debtors:
        return say("total.none")
    return _list("debtors.some", [f"{p.name} {format_inr(p.balance)}" for p in debtors], cap=5)


def stock_list(products: list[Product]) -> str:
    if not products:
        return say("stock_list.none")
    return _list("stock_list.some", [f"{p.name} {qty(p.stock_level)} {unit(p)}" for p in products], cap=8)


def period_total(intent, period, sums: dict, party: Party | None, product: Product | None) -> str:
    when = say(f"period.{period}")
    if product:
        return say(
            "stock_move.product",
            period=when,
            name=product.name,
            moved_in=qty(sums[EntryKind.STOCK_IN]),
            moved_out=qty(sums[EntryKind.STOCK_OUT]),
            unit=unit(product),
        )
    key = {"QUERY_CREDIT_GIVEN": "credit", "QUERY_PAYMENTS_RECEIVED": "payments", "QUERY_STAFF_PAID": "staff_paid"}[intent]
    money = {
        "amount": format_inr(sum(sums.values())),
        "advance": format_inr(sums.get(EntryKind.STAFF_ADVANCE, 0)),
        "salary": format_inr(sums.get(EntryKind.SALARY_PAID, 0)),
    }
    if party:
        return say(f"{key}.party", period=when, name=party.name, **money)
    return say(f"{key}.total", period=when, **money)


def stock_moves(period, products: list[Product]) -> str:
    when = say(f"period.{period}")
    if not products:
        return say("stock_move.none", period=when)
    items = [
        say("stock_move.item", name=p.name, moved_in=qty(p.moved_in), moved_out=qty(p.moved_out), unit=unit(p))
        for p in products
    ]
    return say("stock_move.many", period=when, count=len(products), items="; ".join(items))


def negative_stock(product: Product, level: Decimal) -> str:
    return say("negative_stock", name=product.name, level=qty(level), unit=unit(product))


def choose(labels: list[str]) -> str:
    return f"{f' {say("or")} '.join(labels)}?"


def not_found(name: str, offer_create: bool) -> str:
    return say("not_found.create" if offer_create else "not_found", name=name)


def confirm_amount(amount: Decimal) -> str:
    return say("confirm_amount", amount=format_inr(amount))


def ask_detail(intent: str, missing: str, name: str) -> str:
    return say(f"ask.name.{intent}") if missing == "name" else say(f"ask.{missing}", name=name)


def confirm_new(intent: str, new: dict) -> str:
    if intent == "ADD_PRODUCT":
        units = say(f"unit.{new['unit']}")
        return say("confirm.ADD_PRODUCT", name=new["name"], unit=units, qty=qty(new["quantity"]), alert=qty(new["alert"]))
    if intent == "ADD_STAFF":
        return say("confirm.ADD_STAFF", name=new["name"], amount=format_inr(new["amount"]))
    key = "confirm.ADD_CUSTOMER.owing" if new["amount"] else "confirm.ADD_CUSTOMER"
    return say(key, name=new["name"], amount=format_inr(new["amount"]))


def product_added(product: Product, quantity: Decimal) -> str:
    return say("added.ADD_PRODUCT", name=product.name, qty=qty(Decimal(quantity)), unit=unit(product))


def for_speech(text: str) -> str:
    "₹1,250.50 -> '1250.50 rupaye' (in the reply's language): plain digits and the word read more naturally."
    return re.sub(r"₹([\d,]+(?:\.\d+)?)", lambda m: say("rupees", amount=m.group(1).replace(",", "")), text)
