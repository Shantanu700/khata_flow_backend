import logging
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rapidfuzz import fuzz

from base.exceptions import DomainError
from modules.ledger import services as ledger
from modules.ledger.choices import EntryKind
from modules.ledger.services import format_inr
from modules.shops.choices import SHOP_LANGUAGES, PartyKind, Unit, voice_language
from modules.shops.models import Party, Product
from modules.voice import responses
from modules.voice.audio import normalise
from modules.voice.choices import (
    CREATE_INTENTS,
    CUSTOMER_INTENTS,
    OPTIONAL_CUSTOMER_INTENTS,
    OPTIONAL_PRODUCT_INTENTS,
    OPTIONAL_STAFF_INTENTS,
    PRODUCT_INTENTS,
    STAFF_INTENTS,
    CommandStatus,
    InputMode,
    Intent,
    Period,
)
from modules.voice.intent import ParsedCommand, ShopContext, get_intent_parser
from modules.voice.models import Chat, VoiceCommand
from modules.voice.providers import ProviderError, get_stt, get_tts
from modules.voice.resolver import Outcome, resolve_party, resolve_product
from modules.voice.resolver import normalise as normalise_name
from modules.voice.scripts import script_language

log = logging.getLogger(__name__)

# Set while a request streams its progress: told each stage's name and milliseconds as soon as it finishes.
progress: ContextVar[Callable[[str, int], None] | None] = ContextVar("voice_progress", default=None)

WRITE_KINDS = {
    Intent.ADD_CREDIT: EntryKind.CREDIT_GIVEN,
    Intent.RECORD_PAYMENT: EntryKind.PAYMENT_RECEIVED,
    Intent.STOCK_IN: EntryKind.STOCK_IN,
    Intent.STOCK_OUT: EntryKind.STOCK_OUT,
    Intent.MARK_ATTENDANCE: EntryKind.ATTENDANCE,
    Intent.STAFF_ADVANCE: EntryKind.STAFF_ADVANCE,
    Intent.SALARY_PAID: EntryKind.SALARY_PAID,
}
AMOUNT_INTENTS = {Intent.ADD_CREDIT, Intent.RECORD_PAYMENT, Intent.STAFF_ADVANCE, Intent.SALARY_PAID}
QUANTITY_INTENTS = {Intent.STOCK_IN, Intent.STOCK_OUT}
YES, NO = "yes", "no"
UNITS = {"packet": Unit.PACKET, "kg": Unit.KG, "kilo": Unit.KG, "litre": Unit.LITRE, "liter": Unit.LITRE, "piece": Unit.PIECE}
# The add form's default too; the shopkeeper changes it on the Stock screen.
NEW_PRODUCT_ALERT = Decimal(5)
# Earlier exchanges of the chat the intent model sees: enough for a create with a change or two, at ~30 tokens each.
HISTORY_TURNS = 6


class Stop(Exception):
    "Ends the pipeline with a spoken reply; `code` set means it failed."

    def __init__(self, text: str, code: str = ""):
        super().__init__(code or text)
        self.text = text
        self.code = code


class Cancelled(Exception):
    "The shopkeeper cancelled the command while it ran: stop, and save nothing more."


def _live(command: VoiceCommand, lock: bool = False) -> None:
    "Raises Cancelled once cancel() has run; `lock` holds the row to the end of the transaction, so a cancel waits."
    rows = VoiceCommand.objects.filter(pk=command.pk)
    if (rows.select_for_update() if lock else rows).values_list("stage", flat=True).get() == CommandStatus.CANCELLED:
        command.stage = CommandStatus.CANCELLED
        raise Cancelled


def _save(command: VoiceCommand) -> None:
    with transaction.atomic():
        _live(command, lock=True)
        command.save()


class Timings:
    def __init__(self, command: VoiceCommand):
        self.command = command

    @contextmanager
    def __call__(self, stage: str):
        _live(self.command)
        start = time.perf_counter()
        try:
            yield
        finally:
            ms = self.command.timings_ms[stage] = round((time.perf_counter() - start) * 1000)
            if report := progress.get():
                report(stage, ms)


def _set_stage(command: VoiceCommand, stage: CommandStatus) -> None:
    command.stage = stage
    _save(command)


def _shop_context(command: VoiceCommand) -> ShopContext:
    parties = command.shop.parties.filter(is_active=True)
    return ShopContext(
        customers=[p.name for p in parties if p.kind == PartyKind.CUSTOMER],
        staff=[p.name for p in parties if p.kind == PartyKind.STAFF],
        products=list(command.shop.products.filter(is_active=True).values_list("name", flat=True)),
        today=timezone.localdate(),
    )


def shop_keyterms(shop) -> list[str]:
    "Names plus aliases: shopkeepers say 'aata', not 'Aashirvaad Aata 5kg'."
    rows = [*shop.parties.filter(is_active=True), *shop.products.filter(is_active=True)]
    return list(dict.fromkeys(term for row in rows for term in [row.name, *(row.aliases or [])]))


def _occurred_on(parsed: ParsedCommand) -> date:
    "Trust the LLM's date only within the last 60 days; anything else is today."
    today = timezone.localdate()
    try:
        hinted = date.fromisoformat(parsed.date_hint or "")
    except ValueError:
        return today
    return hinted if today - timedelta(days=60) <= hinted <= today else today


def _ask(command: VoiceCommand, kind: str, question: str, options: list[tuple[str, str]], answers: dict) -> None:
    expires = timezone.now() + timedelta(seconds=settings.CLARIFICATION_TTL_SECONDS)
    command.clarification = {
        "kind": kind,
        "question": question,
        "options": [{"id": option_id, "label": label} for option_id, label in options],
        "answers": answers,
        "expires_at": expires.isoformat(),
    }
    command.response_text = question
    command.stage = CommandStatus.NEEDS_CLARIFICATION


def _party(command: VoiceCommand, parsed: ParsedCommand, answers: dict) -> Party | None:
    "The resolved party, or None after asking the shopkeeper to clarify."
    kind = PartyKind.STAFF if parsed.intent in STAFF_INTENTS | OPTIONAL_STAFF_INTENTS else PartyKind.CUSTOMER
    parties = command.shop.parties.filter(kind=kind, is_active=True)
    if "party_id" in answers:
        return parties.get(pk=answers["party_id"])
    if not parsed.party_name:
        raise Stop(responses.spoken_error("MISSING_NAME"), "MISSING_NAME")
    if answers.get("create_party"):
        party, _ = command.shop.parties.get_or_create(name=parsed.party_name.strip(), defaults={"kind": kind})
        return party
    result = resolve_party(command.shop, parsed.party_name, kind)
    if result.outcome == Outcome.RESOLVED:
        return result.match
    if result.outcome == Outcome.AMBIGUOUS:
        options = [(f"party:{p.pk}", p.name) for p in result.options]
        _ask(command, "choose", responses.choose([p.name for p in result.options]), options, answers)
        return None
    if kind == PartyKind.CUSTOMER and parsed.intent in WRITE_KINDS:
        question = responses.not_found(parsed.party_name, offer_create=True)
        _ask(command, "create_party", question, list(zip((YES, NO), responses.yes_no())), answers)
        return None
    raise Stop(responses.not_found(parsed.party_name, offer_create=False), "NOT_FOUND")


def _product(command: VoiceCommand, parsed: ParsedCommand, answers: dict) -> Product | None:
    if "product_id" in answers:
        return command.shop.products.get(pk=answers["product_id"], is_active=True)
    if not parsed.product_name:
        raise Stop(responses.spoken_error("MISSING_NAME"), "MISSING_NAME")
    result = resolve_product(command.shop, parsed.product_name)
    if result.outcome == Outcome.RESOLVED:
        return result.match
    if result.outcome == Outcome.AMBIGUOUS:
        options = [(f"product:{p.pk}", p.name) for p in result.options]
        _ask(command, "choose", responses.choose([p.name for p in result.options]), options, answers)
        return None
    raise Stop(responses.not_found(parsed.product_name, offer_create=False), "NOT_FOUND")


def _check_numbers(command: VoiceCommand, parsed: ParsedCommand, answers: dict) -> bool:
    "False after asking to confirm an unusually large amount."
    if parsed.intent in AMOUNT_INTENTS:
        if parsed.amount is None or parsed.amount <= 0:
            raise Stop(responses.spoken_error("MISSING_AMOUNT"), "MISSING_AMOUNT")
        if parsed.amount > settings.LEDGER_AMOUNT_CAP and not answers.get("amount_confirmed"):
            _ask(command, "confirm_amount", responses.confirm_amount(parsed.amount), list(zip((YES, NO), responses.yes_no())), answers)
            return False
    if parsed.intent in QUANTITY_INTENTS and (parsed.quantity is None or parsed.quantity <= 0):
        raise Stop(responses.spoken_error("MISSING_QUANTITY"), "MISSING_QUANTITY")
    return True


def _new_details(command: VoiceCommand, parsed: ParsedCommand, answers: dict) -> dict | None:
    "What the new customer, staff member or product is saved with; None after asking for a missing detail or to confirm."
    intent = parsed.intent
    name = " ".join(((parsed.product_name if intent == Intent.ADD_PRODUCT else parsed.party_name) or "").split())
    unit = UNITS.get((parsed.unit or "").strip().lower().removesuffix("s"))
    salary = parsed.amount if parsed.amount and parsed.amount > 0 else None
    missing = (
        "name" if not name
        else "salary" if intent == Intent.ADD_STAFF and not salary
        else "unit" if intent == Intent.ADD_PRODUCT and not unit
        else None
    )
    if missing:
        _ask(command, "fill", responses.ask_detail(intent, missing, name), [(NO, responses.say("cancel"))], answers)
        return None
    rows = command.shop.products if intent == Intent.ADD_PRODUCT else command.shop.parties
    if rows.filter(name__iexact=name).exists():
        raise Stop(responses.say("exists", name=name), "ALREADY_EXISTS")
    new = {
        "name": name,
        "amount": max(parsed.amount or Decimal(0), Decimal(0)),
        "unit": unit,
        "quantity": max(parsed.quantity or Decimal(0), Decimal(0)),
        "alert": NEW_PRODUCT_ALERT if parsed.low_stock_alert is None else max(parsed.low_stock_alert, Decimal(0)),
    }
    if not answers.get("create_confirmed"):
        _ask(command, "confirm_create", responses.confirm_new(intent, new), list(zip((YES, NO), responses.yes_no())), answers)
        return None
    return new


def _create(command: VoiceCommand, intent: Intent, new: dict) -> str:
    shop = command.shop
    command.stage = CommandStatus.COMMITTED
    if intent == Intent.ADD_PRODUCT:
        product = shop.products.create(name=new["name"], unit=new["unit"], low_stock_threshold=new["alert"])
        if new["quantity"]:
            ledger.record_entry(shop, EntryKind.STOCK_IN, product=product, quantity=new["quantity"], voice_command=command)
        return responses.product_added(product, new["quantity"])
    staff = intent == Intent.ADD_STAFF
    party = shop.parties.create(
        name=new["name"],
        kind=PartyKind.STAFF if staff else PartyKind.CUSTOMER,
        monthly_salary=new["amount"] if staff else None,
    )
    if staff:
        return responses.say("added.ADD_STAFF", name=party.name, amount=format_inr(new["amount"]))
    if new["amount"]:
        ledger.record_entry(shop, EntryKind.CREDIT_GIVEN, party=party, amount=new["amount"], voice_command=command)
    return responses.say("added.ADD_CUSTOMER", name=party.name, balance=responses.balance_line(new["amount"]))


def _write(command: VoiceCommand, parsed: ParsedCommand, party: Party | None, product: Product | None) -> str:
    kind = WRITE_KINDS[parsed.intent]
    on = _occurred_on(parsed)
    if kind == EntryKind.ATTENDANCE:
        half = parsed.quantity is not None and parsed.quantity < 1
        entry = ledger.set_attendance(party, on, "half" if half else "present", voice_command=command)
        command.stage = CommandStatus.COMMITTED
        if half:
            return responses.say("attendance_half", name=party.name)
        return responses.attendance_marked(party, already=entry.voice_command_id != command.pk)
    try:
        entry = ledger.record_entry(
            command.shop,
            kind,
            party=party,
            product=product,
            amount=parsed.amount if parsed.intent in AMOUNT_INTENTS else None,
            quantity=parsed.quantity if parsed.intent in QUANTITY_INTENTS else None,
            occurred_on=on,
            voice_command=command,
        )
    except ledger.LedgerError as exc:
        if exc.code == "NEGATIVE_STOCK":
            raise Stop(responses.negative_stock(product, ledger.stock_level(product)), exc.code) from exc
        if exc.code in ("EXCEEDS_EARNED", "EXCEEDS_SALARY"):
            left = ledger.staff_due(party, on).due if exc.code == "EXCEEDS_EARNED" else ledger.salary_left(party, on)
            raise Stop(responses.say(f"error.{exc.code}", amount=ledger.format_inr(max(left, 0))), exc.code) from exc
        raise
    command.stage = CommandStatus.COMMITTED
    if kind == EntryKind.CREDIT_GIVEN:
        return responses.credit_added(party, entry.amount, ledger.customer_balance(party))
    if kind == EntryKind.PAYMENT_RECEIVED:
        return responses.payment_received(party, entry.amount, ledger.customer_balance(party))
    if kind in (EntryKind.STOCK_IN, EntryKind.STOCK_OUT):
        return responses.stock_changed(entry, ledger.stock_level(product))
    return responses.staff_paid(entry, ledger.staff_due(party, entry.occurred_on))


PERIOD_KINDS = {
    Intent.QUERY_CREDIT_GIVEN: (EntryKind.CREDIT_GIVEN,),
    Intent.QUERY_PAYMENTS_RECEIVED: (EntryKind.PAYMENT_RECEIVED,),
    Intent.QUERY_STAFF_PAID: (EntryKind.STAFF_ADVANCE, EntryKind.SALARY_PAID),
    Intent.QUERY_STOCK_MOVEMENT: (EntryKind.STOCK_IN, EntryKind.STOCK_OUT),
}


def period_bounds(period: Period) -> tuple[date | None, date]:
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    return {
        Period.TODAY: (today, today),
        Period.YESTERDAY: (today - timedelta(days=1), today - timedelta(days=1)),
        Period.THIS_WEEK: (monday, today),
        Period.LAST_WEEK: (monday - timedelta(days=7), monday - timedelta(days=1)),
        Period.THIS_MONTH: (first, today),
        Period.LAST_MONTH: ledger.month_bounds(first - timedelta(days=1)),
        Period.ALL: (None, today),
    }[period]


def _staff(shop) -> list[Party]:
    return list(shop.parties.filter(kind=PartyKind.STAFF, is_active=True).order_by("name"))


def _answer(command: VoiceCommand, parsed: ParsedCommand, party: Party | None, product: Product | None) -> str:
    shop, intent = command.shop, parsed.intent
    command.stage = CommandStatus.ANSWERED
    if intent == Intent.QUERY_CUSTOMER_BALANCE:
        return responses.customer_balance(party, ledger.customer_balance(party))
    if intent == Intent.QUERY_TOTAL_OUTSTANDING:
        return responses.total_outstanding(ledger.total_outstanding(shop), len(ledger.top_debtors(shop, limit=None)))
    if intent == Intent.QUERY_LOW_STOCK:
        return responses.low_stock(ledger.low_stock_products(shop))
    if intent == Intent.QUERY_STOCK_LEVEL:
        return responses.stock_level(product, ledger.stock_level(product))
    if intent == Intent.QUERY_STAFF_DUE:
        month = (period_bounds(parsed.period)[0] if parsed.period else None) or _occurred_on(parsed)
        return responses.staff_due(party, ledger.staff_due(party, month), month)
    if intent == Intent.QUERY_STAFF_LIST:
        return responses.staff_list(_staff(shop))
    if intent == Intent.QUERY_ATTENDANCE_TODAY:
        today = timezone.localdate()
        staff = _staff(shop)
        present = [p for p in staff if ledger.attendance_entry_id(p, today)]
        return responses.attendance_today(staff, present)
    if intent == Intent.QUERY_CUSTOMER_COUNT:
        customers = shop.parties.filter(kind=PartyKind.CUSTOMER, is_active=True).count()
        return responses.customer_count(customers, len(ledger.top_debtors(shop, limit=None)))
    if intent == Intent.QUERY_DEBTOR_LIST:
        return responses.debtor_list(ledger.top_debtors(shop, limit=None))
    if intent == Intent.QUERY_STOCK_LIST:
        return responses.stock_list(list(ledger.with_stock_level(shop.products.filter(is_active=True)).order_by("name")))
    if intent in PERIOD_KINDS:
        period = parsed.period or (Period.ALL if party else Period.TODAY)
        start, end = period_bounds(period)
        if intent == Intent.QUERY_STOCK_MOVEMENT and not product:
            return responses.stock_moves(period, ledger.stock_moves(shop, start, end))
        sums = ledger.totals(shop, PERIOD_KINDS[intent], start, end, party=party, product=product)
        return responses.period_total(intent, period, sums, party, product)
    # Never by voice: a misheard undo reverses the wrong entry. The Entries screen has an Undo on each row.
    if intent == Intent.UNDO_LAST:
        return responses.say("undo.by_hand")
    raise Stop(responses.fallback(), "UNKNOWN_INTENT")


def _resolve(command: VoiceCommand, parsed: ParsedCommand, answers: dict) -> Callable[[], str] | None:
    "The write or answer to run, or None after asking the shopkeeper something."
    if parsed.intent in CREATE_INTENTS:
        new = _new_details(command, parsed, answers)
        return new and (lambda: _create(command, parsed.intent, new))
    named = parsed.intent in CUSTOMER_INTENTS | STAFF_INTENTS
    optional = parsed.intent in OPTIONAL_CUSTOMER_INTENTS | OPTIONAL_STAFF_INTENTS and parsed.party_name
    party = _party(command, parsed, answers) if named or optional else None
    if command.stage == CommandStatus.NEEDS_CLARIFICATION:
        return None
    optional = parsed.intent in OPTIONAL_PRODUCT_INTENTS and parsed.product_name
    product = _product(command, parsed, answers) if parsed.intent in PRODUCT_INTENTS or optional else None
    if command.stage == CommandStatus.NEEDS_CLARIFICATION or not _check_numbers(command, parsed, answers):
        return None
    act = _write if parsed.intent in WRITE_KINDS else _answer
    return lambda: act(command, parsed, party, product)


def _resolve_and_act(command: VoiceCommand, parsed: ParsedCommand, answers: dict, timings: Timings) -> None:
    with timings("resolve"):
        act = _resolve(command, parsed, answers)
    if act is None:
        return
    # Under the row lock: a cancel lands either before the write (which then never happens) or after (and reverses it).
    with timings("commit"), transaction.atomic():
        _live(command, lock=True)
        command.response_text = act()
        command.clarification = None


def _fail(command: VoiceCommand, code: str, text: str, detail: str = "") -> None:
    command.stage = CommandStatus.FAILED
    command.error_code = code
    command.error_detail = detail
    command.response_text = text
    command.clarification = None


def _run(command: VoiceCommand, timings: Timings, stages) -> None:
    # Replies are written in the command's language (responses.language).
    reset = responses.language.set(command.language if command.language in SHOP_LANGUAGES else responses.BASE)
    try:
        _stages(command, timings, stages)
    finally:
        responses.language.reset(reset)


def _stages(command: VoiceCommand, timings: Timings, stages) -> None:
    try:
        stages()
    except Cancelled:
        return
    except Stop as stop:
        if stop.code:
            _fail(command, stop.code, stop.text)
        else:
            command.response_text = stop.text
    except ProviderError as exc:
        log.warning("voice command %s failed: %s", command.pk, exc)
        _fail(command, exc.code, responses.spoken_error(exc.code), exc.detail)
    except DomainError as exc:
        _fail(command, exc.code, responses.spoken_error(exc.code), exc.message)
    except Exception as exc:
        log.exception("voice command %s crashed", command.pk)
        _fail(command, "INTERNAL_ERROR", responses.fallback(), type(exc).__name__)
    try:
        _speak(command, timings)
        _save(command)
    except Cancelled:
        pass


def _speak(command: VoiceCommand, timings: Timings) -> None:
    if not command.response_text:
        return
    try:
        with timings("tts"):
            audio = get_tts().synthesize(responses.for_speech(command.response_text), voice_language(command.language))
    except ProviderError as exc:
        log.warning("tts for command %s failed: %s", command.pk, exc)
        command.error_detail = f"{command.error_detail} tts:{exc.code}".strip()
        return
    command.response_audio.save(f"{command.pk}.wav", ContentFile(audio), save=False)


# Beyond each language's own Yes/No labels: what shopkeepers also type or say.
YES_WORDS = {"yes", "y", "ok", "okay", "sure", "haan", "han", "ha", "haa", "hanji", "ji", "theek", "thik", "jod", "kar", "add"}
NO_WORDS = {"no", "n", "nahi", "nahin", "nai", "na", "mat", "cancel", "rehne", "ruko", "stop"}
FILLER_WORDS = {"ji", "do", "dijiye", "karo", "please", "thanks", "it", "hai", "sahi", "bilkul"}


def _yes_no(text: str) -> str | None:
    """YES or NO for a reply made only of yes (or only of no) words, such as 'haan jod do' or 'no thanks'.
    Anything more ('nahi, salary 3000') is None: a detail to change, not an answer."""
    # Only punctuation goes: \W would also strip Devanagari vowel signs (हाँ).
    words = re.sub(r"[.,!?;:'\"।|]", " ", text.lower()).split()
    catalogs = [responses.catalog(code) for code in SHOP_LANGUAGES]
    yes = YES_WORDS | {c["yes"].lower() for c in catalogs}
    no = NO_WORDS | {c["no"].lower() for c in catalogs}
    # A tapped button sends its label: "रहने दो" (Cancel) is a no as a whole, though "दो" alone is not.
    if " ".join(words) in {" ".join(c["cancel"].lower().split()) for c in catalogs}:
        return NO
    if words and set(words) <= yes | FILLER_WORDS and set(words) & yes:
        return YES
    if words and set(words) <= no | FILLER_WORDS and set(words) & no:
        return NO
    return None


def _pick(text: str, options: list[dict]) -> str | None:
    "The option a reply names ('Gali wale Sharma ji', or a tapped button's label), when one clearly beats the rest."
    exact = [o["id"] for o in options if o["label"].casefold().split() == text.casefold().split()]
    if len(exact) == 1:
        return exact[0]
    said = normalise_name(text)
    ranked = sorted(((fuzz.WRatio(said, normalise_name(o["label"])), o["id"]) for o in options), reverse=True)
    if not ranked or ranked[0][0] < settings.RESOLVER_AUTO_SCORE:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] <= settings.RESOLVER_MARGIN:
        return None
    return ranked[0][1]


def _take_question(command: VoiceCommand) -> tuple[dict, dict, str | None] | None:
    """The question the chat's previous message still waits on, when this message answers it: (clarification,
    its parsed command, the choice: YES/NO or an option id, or None for a missing detail or a change to the
    details). That question is closed; this message carries on from it."""
    if not command.chat_id:
        return None
    with transaction.atomic():
        earlier = command.chat.messages.select_for_update().filter(pk__lt=command.pk).order_by("-id")
        pending = earlier.first()
        asked = pending and pending.stage == CommandStatus.NEEDS_CLARIFICATION and pending.clarification
        if not asked or timezone.now() > datetime.fromisoformat(asked["expires_at"]):
            return None
        answer = _yes_no(command.transcript)
        if asked["kind"] == "fill":
            answer = NO if answer == NO else None
        elif asked["kind"] == "confirm_create":
            pass  # neither yes nor no: a change to the details read back, parsed with the chat so far
        elif asked["kind"] == "choose":
            answer = answer if answer == NO else _pick(command.transcript, asked["options"])
            if answer is None:
                return None
        elif answer is None or {option["id"] for option in asked["options"]} != {YES, NO}:
            return None
        pending.stage = CommandStatus.ANSWERED
        pending.clarification = None
        pending.save(update_fields=["stage", "clarification", "updated_at"])
    return asked, pending.parsed, answer


def _resume(command: VoiceCommand, pending: dict, choice: str, timings: Timings) -> None:
    "Carry on from the question `pending` with the shopkeeper's answer."
    if choice == NO:
        command.stage = CommandStatus.ANSWERED
        command.clarification = None
        raise Stop(responses.spoken_error("CANCELLED"))
    answers = dict(pending.get("answers", {}))
    if pending["kind"] == "create_party":
        answers["create_party"] = True
    elif pending["kind"] == "confirm_amount":
        answers["amount_confirmed"] = True
    elif pending["kind"] == "confirm_create":
        answers["create_confirmed"] = True
    else:
        field, pk = choice.split(":")
        answers[f"{field}_id"] = int(pk)
    command.stage = CommandStatus.PARSED
    _resolve_and_act(command, ParsedCommand.model_validate(command.parsed), answers, timings)


def _history(command: VoiceCommand) -> list[tuple[str, str]]:
    if not command.chat_id:
        return []
    rows = command.chat.messages.filter(pk__lt=command.pk).exclude(transcript="").order_by("-id")[:HISTORY_TURNS]
    return [(row.transcript, row.response_text) for row in reversed(rows)]


def _file_in_chat(command: VoiceCommand) -> None:
    "The chat moves to the top of the list; its first words become its title."
    if not command.chat_id:
        return
    fields = {"last_message_at": timezone.now()}
    if not command.chat.title and command.transcript.strip():
        fields["title"] = " ".join(command.transcript.split())[:120]
    Chat.objects.filter(pk=command.chat_id).update(**fields)
    for name, value in fields.items():
        setattr(command.chat, name, value)


def handle_command(command: VoiceCommand) -> VoiceCommand:
    "One message in its chat; it may answer the question the chat's previous message asked."
    timings = Timings(command)

    def stages() -> None:
        context = _shop_context(command)
        if command.input_mode == InputMode.AUDIO:
            with timings("normalise"):
                command.audio.open("rb")
                wav, _ = normalise(command.audio.read())
                command.audio.close()
            with timings("stt"):
                transcript = get_stt().transcribe(wav, "audio/wav", voice_language(command.language), keyterms=shop_keyterms(command.shop))
            command.transcript = transcript.text
            command.stt_confidence = transcript.confidence
            command.detected_language = transcript.language
            _set_stage(command, CommandStatus.TRANSCRIBED)
        elif command.input_mode == InputMode.TEXT:
            command.detected_language = script_language(command.transcript)
        _file_in_chat(command)
        if not command.transcript.strip():
            raise Stop(responses.spoken_error("EMPTY_TRANSCRIPT"), "EMPTY_TRANSCRIPT")
        question = _take_question(command)
        if question and question[2]:
            command.parsed = question[1]
            _resume(command, question[0], question[2], timings)
            return
        # "8000" means something only after "What is Raju's salary?": the model reads the chat so far.
        with timings("intent"):
            parsed = get_intent_parser().parse(command.transcript.strip(), context, _history(command))
        command.parsed = parsed.model_dump(mode="json")
        _set_stage(command, CommandStatus.PARSED)
        if parsed.intent == Intent.UNKNOWN:
            raise Stop(responses.fallback(), "UNKNOWN_INTENT")
        _resolve_and_act(command, parsed, {}, timings)

    _run(command, timings, stages)
    return command


def clarify(command: VoiceCommand, option_id: str | None = None, confirm: bool | None = None) -> VoiceCommand:
    "Resume a NEEDS_CLARIFICATION command from stage 5 with the shopkeeper's answer."
    if command.stage != CommandStatus.NEEDS_CLARIFICATION or not command.clarification:
        raise DomainError("NOT_WAITING", "This command is not waiting for an answer.")
    timings = Timings(command)
    pending = command.clarification
    choice = option_id or (YES if confirm else NO if confirm is not None else None)
    valid = {option["id"] for option in pending["options"]}
    if choice not in valid:
        raise DomainError("INVALID_OPTION", f"Answer with one of: {', '.join(sorted(valid))}.")

    def stages() -> None:
        if timezone.now() > datetime.fromisoformat(pending["expires_at"]):
            raise Stop(responses.spoken_error("CLARIFICATION_EXPIRED"), "CLARIFICATION_EXPIRED")
        _resume(command, pending, choice, timings)

    _run(command, timings, stages)
    return command



def cancel(command: VoiceCommand) -> VoiceCommand:
    "Stop a running command. Anything it already wrote is reversed, append-only like any undo."
    with transaction.atomic():
        command = VoiceCommand.objects.select_for_update().get(pk=command.pk)
        if command.stage == CommandStatus.CANCELLED:
            return command
        for entry in command.entries.all() & ledger.live_entries():
            ledger.reverse_entry(entry, voice_command=command)
        command.stage = CommandStatus.CANCELLED
        command.clarification = None
        command.save(update_fields=["stage", "clarification", "updated_at"])
    return command
