from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry
from modules.ledger.services import LedgerError, live_entries, record_entry, set_attendance, staff_due

D = Decimal


def today() -> date:
    return timezone.localdate()


def test_a_half_day_earns_half_a_days_pay(shop, ramu):
    november = date(2026, 11, 15)
    for day in (date(2026, 11, 2), date(2026, 11, 3)):
        record_entry(shop, EntryKind.ATTENDANCE, party=ramu, occurred_on=day)
    Entry.objects.create(shop=shop, kind=EntryKind.ATTENDANCE, party=ramu, quantity=D("0.5"), occurred_on=date(2026, 11, 4))
    due = staff_due(ramu, november)
    assert due.days_present == D("2.5")
    assert due.earned == D("750")  # 9000 x 2.5 / 30


def test_a_day_moves_between_present_half_and_absent_as_corrections(shop, ramu):
    day = today()
    set_attendance(ramu, day, "present")
    set_attendance(ramu, day, "present")
    assert staff_due(ramu, day).days_present == 1
    set_attendance(ramu, day, "half")
    assert staff_due(ramu, day).days_present == D("0.5")
    set_attendance(ramu, day, "absent")
    assert staff_due(ramu, day).days_present == 0
    assert not live_entries().filter(kind=EntryKind.ATTENDANCE).exists()
    # Nothing erased: the full day and the half day are both still there, each reversed.
    assert Entry.objects.filter(kind=EntryKind.ATTENDANCE).count() == 2
    assert Entry.objects.filter(kind=EntryKind.REVERSAL).count() == 2


def test_attendance_can_be_set_for_this_month_and_last_month_only(shop, ramu):
    first_of_last_month = (today().replace(day=1) - timedelta(days=1)).replace(day=1)
    set_attendance(ramu, first_of_last_month, "half")
    for day in (first_of_last_month - timedelta(days=1), today() + timedelta(days=1)):
        with pytest.raises(LedgerError) as err:
            set_attendance(ramu, day, "present")
        assert err.value.code == "ATTENDANCE_WINDOW"


def test_attendance_is_a_full_or_a_half_day(shop, ramu, sharma):
    with pytest.raises(LedgerError) as err:
        record_entry(shop, EntryKind.ATTENDANCE, party=ramu, quantity=D("2"))
    assert err.value.code == "INVALID_QUANTITY"
    with pytest.raises(LedgerError) as err:
        set_attendance(sharma, today(), "present")
    assert err.value.code == "WRONG_PARTY_KIND"


def test_the_screen_sets_a_day_and_sees_the_half_days(shop, ramu, django_user_model):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=shop.owner).key}")
    yesterday = (today() - timedelta(days=1)).isoformat()
    body = api.put(f"/api/v1/parties/{ramu.pk}/attendance/", {"on": yesterday, "mark": "half"}, format="json").json()
    assert body["attendance_14d"] == [yesterday] and body["half_days_14d"] == [yesterday]
    month = api.get(f"/api/v1/parties/{ramu.pk}/month/", {"month": yesterday[:7]}).json()
    assert month["half_days"] == [yesterday] and month["days_present"] == 0.5
    response = api.put(f"/api/v1/parties/{ramu.pk}/attendance/", {"on": "2020-01-01", "mark": "present"}, format="json")
    assert response.json()["error"]["code"] == "ATTENDANCE_WINDOW"
    assert api.patch(f"/api/v1/parties/{ramu.pk}/attendance/", {}, format="json").status_code == 405
