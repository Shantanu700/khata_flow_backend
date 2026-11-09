from datetime import date
from decimal import Decimal

import pytest

from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry
from modules.ledger.services import (
    LedgerError,
    customer_balance,
    format_inr,
    low_stock_products,
    record_entry,
    reverse_entry,
    staff_due,
    stock_level,
    total_outstanding,
    undo_last,
)

D = Decimal


def test_customer_balance_is_credit_minus_payments(shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("500"))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("750"))
    record_entry(shop, EntryKind.PAYMENT_RECEIVED, party=sharma, amount=D("200"))
    assert customer_balance(sharma) == D("1050")


def test_total_outstanding_counts_only_positive_balances(shop, sharma, verma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("500"))
    record_entry(shop, EntryKind.PAYMENT_RECEIVED, party=verma, amount=D("300"))
    assert customer_balance(verma) == D("-300")
    assert total_outstanding(shop) == D("500")


def test_reversed_entries_and_reversals_are_excluded(shop, sharma):
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("500"))
    mistake = record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("5000"))
    reversal = undo_last(shop)
    assert reversal.kind == EntryKind.REVERSAL and reversal.reverses == mistake
    assert customer_balance(sharma) == D("500")
    assert Entry.objects.count() == 3


def test_undo_last_skips_already_reversed_entries(shop, sharma):
    first = record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("100"))
    record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("200"))
    undo_last(shop)
    assert undo_last(shop).reverses == first
    with pytest.raises(LedgerError) as err:
        undo_last(shop)
    assert err.value.code == "NOTHING_TO_UNDO"


def test_entry_cannot_be_reversed_twice(shop, sharma):
    entry = record_entry(shop, EntryKind.CREDIT_GIVEN, party=sharma, amount=D("100"))
    reversal = reverse_entry(entry)
    with pytest.raises(LedgerError, match="already undone"):
        reverse_entry(entry)
    with pytest.raises(LedgerError):
        reverse_entry(reversal)


def test_stock_level_and_low_stock(shop, aata, maggi):
    record_entry(shop, EntryKind.STOCK_IN, product=aata, quantity=D("20"))
    record_entry(shop, EntryKind.STOCK_OUT, product=aata, quantity=D("6"))
    record_entry(shop, EntryKind.STOCK_IN, product=maggi, quantity=D("10"))
    assert stock_level(aata) == D("14")
    assert [p.name for p in low_stock_products(shop)] == ["Maggi"]


def test_stock_out_cannot_go_negative(shop, aata):
    record_entry(shop, EntryKind.STOCK_IN, product=aata, quantity=D("3"))
    with pytest.raises(LedgerError) as err:
        record_entry(shop, EntryKind.STOCK_OUT, product=aata, quantity=D("4"))
    assert err.value.code == "NEGATIVE_STOCK"
    assert stock_level(aata) == D("3")


def test_undoing_stock_in_cannot_make_stock_negative(shop, aata):
    record_entry(shop, EntryKind.STOCK_IN, product=aata, quantity=D("5"))
    record_entry(shop, EntryKind.STOCK_OUT, product=aata, quantity=D("4"))
    with pytest.raises(LedgerError) as err:
        reverse_entry(Entry.objects.get(kind=EntryKind.STOCK_IN))
    assert err.value.code == "NEGATIVE_STOCK"


def test_attendance_twice_a_day_returns_the_existing_entry(shop, ramu):
    day = date(2026, 11, 3)
    first = record_entry(shop, EntryKind.ATTENDANCE, party=ramu, occurred_on=day)
    again = record_entry(shop, EntryKind.ATTENDANCE, party=ramu, occurred_on=day)
    assert again.pk == first.pk
    assert Entry.objects.filter(kind=EntryKind.ATTENDANCE).count() == 1


def test_wrong_party_kind_is_rejected(shop, sharma, ramu):
    with pytest.raises(LedgerError) as err:
        record_entry(shop, EntryKind.ATTENDANCE, party=sharma)
    assert err.value.code == "WRONG_PARTY_KIND"
    with pytest.raises(LedgerError):
        record_entry(shop, EntryKind.CREDIT_GIVEN, party=ramu, amount=D("100"))


def test_staff_due_counts_only_the_requested_month(shop, ramu):
    for day in (date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)):
        record_entry(shop, EntryKind.ATTENDANCE, party=ramu, occurred_on=day)
    record_entry(shop, EntryKind.STAFF_ADVANCE, party=ramu, amount=D("500"), occurred_on=date(2026, 10, 31))
    record_entry(shop, EntryKind.STAFF_ADVANCE, party=ramu, amount=D("100"), occurred_on=date(2026, 11, 2))
    record_entry(shop, EntryKind.SALARY_PAID, party=ramu, amount=D("200"), occurred_on=date(2026, 11, 3))

    november = staff_due(ramu, date(2026, 11, 15))
    assert (november.days_present, november.days_in_month) == (3, 30)
    assert november.earned == D("900")
    assert november.due == D("600")

    october = staff_due(ramu, date(2026, 10, 1))
    assert october.days_present == 2
    assert october.earned == D("581")
    assert october.due == D("81")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (D("0"), "₹0"),
        (D("999"), "₹999"),
        (D("1000"), "₹1,000"),
        (D("1250"), "₹1,250"),
        (D("100000"), "₹1,00,000"),
        (D("125000"), "₹1,25,000"),
        (D("12345678"), "₹1,23,45,678"),
        (D("1250.5"), "₹1,250.50"),
        (D("0.05"), "₹0.05"),
        (D("-300"), "-₹300"),
    ],
)
def test_format_inr(amount, expected):
    assert format_inr(amount) == expected
