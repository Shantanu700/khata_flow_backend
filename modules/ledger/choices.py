from django.db.models import TextChoices


class EntryKind(TextChoices):
    CREDIT_GIVEN = "CREDIT_GIVEN", "Credit given"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED", "Payment received"
    STOCK_IN = "STOCK_IN", "Stock in"
    STOCK_OUT = "STOCK_OUT", "Stock out"
    ATTENDANCE = "ATTENDANCE", "Attendance"
    STAFF_ADVANCE = "STAFF_ADVANCE", "Staff advance"
    SALARY_PAID = "SALARY_PAID", "Salary paid"
    REVERSAL = "REVERSAL", "Reversal"


CUSTOMER_MONEY_KINDS = (EntryKind.CREDIT_GIVEN, EntryKind.PAYMENT_RECEIVED)
STOCK_KINDS = (EntryKind.STOCK_IN, EntryKind.STOCK_OUT)
STAFF_MONEY_KINDS = (EntryKind.STAFF_ADVANCE, EntryKind.SALARY_PAID)
