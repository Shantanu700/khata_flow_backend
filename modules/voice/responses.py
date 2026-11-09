import re
from decimal import Decimal

from modules.ledger.choices import EntryKind
from modules.ledger.models import Entry
from modules.ledger.services import StaffDue, format_inr
from modules.shops.choices import Unit
from modules.shops.models import Party, Product

UNIT_WORDS = {Unit.PACKET: "packet", Unit.KG: "kilo", Unit.LITRE: "litre", Unit.PIECE: "piece"}
COUNT_WORDS = ["", "Ek", "Do", "Teen", "Chaar", "Paanch", "Chhah", "Saat", "Aath", "Nau", "Das"]

FALLBACK = "Maaf kijiye, samajh nahi aaya. Dobara boliye."
SPOKEN_ERRORS = {
    "EMPTY_TRANSCRIPT": "Kuch sunai nahi diya. Dobara boliye.",
    "AUDIO_TOO_SHORT": "Awaaz bahut chhoti thi. Dobara boliye.",
    "AUDIO_TOO_LONG": "Thoda chhota boliye, bees second se kam.",
    "MISSING_NAME": "Kiska naam? Naam ke saath dobara boliye.",
    "MISSING_AMOUNT": "Kitne rupaye? Poori baat dobara boliye.",
    "MISSING_QUANTITY": "Kitna saamaan? Poori baat dobara boliye.",
    "NOTHING_TO_UNDO": "Hatane ke liye koi entry nahi hai.",
    "CLARIFICATION_EXPIRED": "Samay khatam ho gaya. Poori baat dobara boliye.",
    "CANCELLED": "Theek hai, kuch nahi likha.",
}


def spoken_error(code: str) -> str:
    return SPOKEN_ERRORS.get(code, FALLBACK)


def qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def unit(product: Product) -> str:
    return UNIT_WORDS.get(product.unit, "")


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} aur {names[-1]}"


def count_word(n: int) -> str:
    return COUNT_WORDS[n] if n < len(COUNT_WORDS) else str(n)


def balance_line(balance: Decimal) -> str:
    if balance > 0:
        return f"Ab kul baaki {format_inr(balance)} hai."
    if balance < 0:
        return f"Ab {format_inr(-balance)} advance jama hai."
    return "Ab koi baaki nahi hai."


def credit_added(party: Party, amount: Decimal, balance: Decimal) -> str:
    return f"Theek hai. {party.name} ka {format_inr(amount)} udhaar likh diya. {balance_line(balance)}"


def payment_received(party: Party, amount: Decimal, balance: Decimal) -> str:
    return f"{party.name} se {format_inr(amount)} mil gaye. {balance_line(balance)}"


def stock_changed(entry: Entry, level: Decimal) -> str:
    product = entry.product
    verb = "jod diye" if entry.kind == EntryKind.STOCK_IN else "ghata diye"
    return f"{product.name} ke {qty(entry.quantity)} {unit(product)} {verb}. Ab stock {qty(level)} {unit(product)}."


def attendance_marked(party: Party, already: bool) -> str:
    if already:
        return f"{party.name} ki haaziri pehle se lagi hai."
    return f"{party.name} ki haaziri lag gayi."


def staff_paid(entry: Entry, due: StaffDue) -> str:
    what = "advance" if entry.kind == EntryKind.STAFF_ADVANCE else "tankhwah"
    return f"{entry.party.name} ko {format_inr(entry.amount)} {what} likh diya. Is mahine {format_inr(due.due)} dena baaki hai."


def customer_balance(party: Party, balance: Decimal) -> str:
    if balance > 0:
        return f"{party.name} ka {format_inr(balance)} baaki hai."
    if balance < 0:
        return f"{party.name} ka {format_inr(-balance)} advance jama hai."
    return f"{party.name} ka koi baaki nahi hai."


def total_outstanding(total: Decimal, debtors: int) -> str:
    if total <= 0:
        return "Kisi par koi udhaar baaki nahi hai."
    return f"Kul {format_inr(total)} udhaar baaki hai, {debtors} logon par."


def low_stock(products: list[Product]) -> str:
    if not products:
        return "Sab saamaan theek hai, kuch kam nahi hai."
    names = join_names([p.name for p in products])
    if len(products) == 1:
        return f"Ek cheez kam hai: {names}."
    return f"{count_word(len(products))} cheezein kam hain: {names}."


def stock_level(product: Product, level: Decimal) -> str:
    return f"{product.name} ka stock {qty(level)} {unit(product)} hai."


def staff_due(party: Party, due: StaffDue) -> str:
    parts = [f"{format_inr(due.earned)} bane"]
    if due.advances:
        parts.append(f"{format_inr(due.advances)} advance gaya")
    if due.salary_paid:
        parts.append(f"{format_inr(due.salary_paid)} tankhwah di")
    owed = f"{format_inr(due.due)} dena baaki hai" if due.due >= 0 else f"{format_inr(-due.due)} zyada de diye"
    return f"{party.name} is mahine {due.days_present} din aaye. {', '.join(parts)}, {owed}."


def describe(entry: Entry) -> str:
    kind, party, product = entry.kind, entry.party, entry.product
    if kind == EntryKind.CREDIT_GIVEN:
        return f"{party.name} ka {format_inr(entry.amount)} udhaar"
    if kind == EntryKind.PAYMENT_RECEIVED:
        return f"{party.name} se {format_inr(entry.amount)} mile"
    if kind in (EntryKind.STOCK_IN, EntryKind.STOCK_OUT):
        verb = "aaye" if kind == EntryKind.STOCK_IN else "nikle"
        return f"{product.name} ke {qty(entry.quantity)} {unit(product)} {verb}"
    if kind == EntryKind.ATTENDANCE:
        return f"{party.name} ki haaziri"
    what = "advance" if kind == EntryKind.STAFF_ADVANCE else "tankhwah"
    return f"{party.name} ko {format_inr(entry.amount)} {what}"


def undone(reversal: Entry) -> str:
    return f"Pichhli entry hata di: {describe(reversal.reverses)}."


def negative_stock(product: Product, level: Decimal) -> str:
    return f"{product.name} ka stock sirf {qty(level)} {unit(product)} hai. Itna nahi nikal sakta."


def choose(labels: list[str]) -> str:
    return f"{' ya '.join(labels)}?"


def not_found(name: str, offer_create: bool) -> str:
    if offer_create:
        return f"Ye naam nahi mila: {name}. Naya customer banaun?"
    return f"Ye naam nahi mila: {name}. Dobara boliye."


def confirm_amount(amount: Decimal) -> str:
    return f"{format_inr(amount)} bahut zyada hai. Pakka likh doon?"


def for_speech(text: str) -> str:
    "₹1,250.50 -> 1250.50 rupaye: plain digits and the word read more naturally in Hinglish TTS."
    return re.sub(r"₹([\d,]+(?:\.\d+)?)", lambda m: f"{m.group(1).replace(',', '')} rupaye", text)
