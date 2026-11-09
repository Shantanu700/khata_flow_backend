import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from base.exceptions import DomainError
from modules.ledger import services as ledger
from modules.ledger.choices import EntryKind
from modules.shops.choices import SHOP_LANGUAGES, PartyKind, voice_language
from modules.shops.models import Party, Product
from modules.voice import responses
from modules.voice.audio import normalise
from modules.voice.choices import (
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
from modules.voice.models import VoiceCommand
from modules.voice.providers import ProviderError, get_stt, get_tts
from modules.voice.resolver import Outcome, resolve_party, resolve_product

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


class Stop(Exception):
    "Ends the pipeline with a spoken reply; `code` set means it failed."

    def __init__(self, text: str, code: str = ""):
        super().__init__(code or text)
        self.text = text
        self.code = code


class Timings:
    def __init__(self, command: VoiceCommand):
        self.command = command

    @contextmanager
    def __call__(self, stage: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            ms = self.command.timings_ms[stage] = round((time.perf_counter() - start) * 1000)
            if report := progress.get():
                report(stage, ms)


def _set_stage(command: VoiceCommand, stage: CommandStatus) -> None:
    command.stage = stage
    command.save()


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


def _write(command: VoiceCommand, parsed: ParsedCommand, party: Party | None, product: Product | None) -> str:
    kind = WRITE_KINDS[parsed.intent]
    on = _occurred_on(parsed)
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
        raise
    command.stage = CommandStatus.COMMITTED
    if kind == EntryKind.CREDIT_GIVEN:
        return responses.credit_added(party, entry.amount, ledger.customer_balance(party))
    if kind == EntryKind.PAYMENT_RECEIVED:
        return responses.payment_received(party, entry.amount, ledger.customer_balance(party))
    if kind in (EntryKind.STOCK_IN, EntryKind.STOCK_OUT):
        return responses.stock_changed(entry, ledger.stock_level(product))
    if kind == EntryKind.ATTENDANCE:
        return responses.attendance_marked(party, already=entry.voice_command_id != command.pk)
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
    if intent == Intent.UNDO_LAST:
        command.stage = CommandStatus.COMMITTED
        return responses.undone(ledger.undo_last(shop, voice_command=command))
    raise Stop(responses.fallback(), "UNKNOWN_INTENT")


def _resolve_and_act(command: VoiceCommand, parsed: ParsedCommand, answers: dict, timings: Timings) -> None:
    with timings("resolve"):
        named = parsed.intent in CUSTOMER_INTENTS | STAFF_INTENTS
        optional = parsed.intent in OPTIONAL_CUSTOMER_INTENTS | OPTIONAL_STAFF_INTENTS and parsed.party_name
        party = _party(command, parsed, answers) if named or optional else None
        if command.stage == CommandStatus.NEEDS_CLARIFICATION:
            return
        optional = parsed.intent in OPTIONAL_PRODUCT_INTENTS and parsed.product_name
        product = _product(command, parsed, answers) if parsed.intent in PRODUCT_INTENTS or optional else None
        if command.stage == CommandStatus.NEEDS_CLARIFICATION:
            return
        if not _check_numbers(command, parsed, answers):
            return
    with timings("commit"):
        act = _write if parsed.intent in WRITE_KINDS else _answer
        command.response_text = act(command, parsed, party, product)
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
    _speak(command, timings)
    command.save()


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


def handle_command(command: VoiceCommand) -> VoiceCommand:
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
            _set_stage(command, CommandStatus.TRANSCRIBED)
        if not command.transcript.strip():
            raise Stop(responses.spoken_error("EMPTY_TRANSCRIPT"), "EMPTY_TRANSCRIPT")
        with timings("intent"):
            parsed = get_intent_parser().parse(command.transcript, context)
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
    answers = dict(pending.get("answers", {}))
    choice = option_id or (YES if confirm else NO if confirm is not None else None)
    valid = {option["id"] for option in pending["options"]}
    if choice not in valid:
        raise DomainError("INVALID_OPTION", f"Answer with one of: {', '.join(sorted(valid))}.")

    def stages() -> None:
        if timezone.now() > datetime.fromisoformat(pending["expires_at"]):
            raise Stop(responses.spoken_error("CLARIFICATION_EXPIRED"), "CLARIFICATION_EXPIRED")
        if choice == NO:
            command.stage = CommandStatus.ANSWERED
            command.clarification = None
            raise Stop(responses.spoken_error("CANCELLED"))
        if pending["kind"] == "create_party":
            answers["create_party"] = True
        elif pending["kind"] == "confirm_amount":
            answers["amount_confirmed"] = True
        else:
            field, pk = choice.split(":")
            answers[f"{field}_id"] = int(pk)
        command.stage = CommandStatus.PARSED
        _resolve_and_act(command, ParsedCommand.model_validate(command.parsed), answers, timings)

    _run(command, timings, stages)
    return command

