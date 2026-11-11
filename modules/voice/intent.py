import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol

import httpx
from django.conf import settings
from pydantic import BaseModel, ConfigDict, ValidationError

from modules.voice.choices import Intent, Period
from modules.voice.providers.base import ProviderError

log = logging.getLogger(__name__)

MAX_RATE_LIMIT_WAIT = 10.0
PLAIN_JSON_HINT = (
    "\nReply with only one JSON object with exactly these keys: intent, party_name, product_name, amount, "
    "quantity, unit, date_hint, period, low_stock_alert. Use null for unused keys. No other text."
)


class ParsedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    party_name: str | None = None
    product_name: str | None = None
    amount: Decimal | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    date_hint: str | None = None
    period: Period | None = None
    low_stock_alert: Decimal | None = None


@dataclass
class ShopContext:
    customers: list[str] = field(default_factory=list)
    staff: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    today: date = field(default_factory=date.today)


# Earlier exchanges in the chat, oldest first: (what the shopkeeper said, what Khata replied).
History = Sequence[tuple[str, str]]


class IntentParser(Protocol):
    def parse(self, transcript: str, context: ShopContext, history: History = ()) -> ParsedCommand: ...


def _nullable(kind: str) -> dict:
    return {"type": [kind, "null"]}


# Groq strict mode: every property required, nulls spelled out, no extra keys.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [intent.value for intent in Intent]},
        "party_name": _nullable("string"),
        "product_name": _nullable("string"),
        "amount": _nullable("number"),
        "quantity": _nullable("number"),
        "unit": _nullable("string"),
        "date_hint": _nullable("string"),
        "period": {"type": ["string", "null"], "enum": [*(p.value for p in Period), None]},
        "low_stock_alert": _nullable("number"),
    },
    "required": list(ParsedCommand.model_fields),
    "additionalProperties": False,
}

INSTRUCTIONS = """\
You turn one spoken command from an Indian kirana shopkeeper into JSON. The command is in Hindi, \
Hinglish, English, Bengali, Gujarati, Kannada, Malayalam, Marathi, Punjabi, Tamil or Telugu, often mixed with \
English words; speech-to-text writes each language in its own script and numbers as digits (sometimes native \
digits such as ৫০০ or ௫௦௦) or ₹. The intents and rules below are the same whatever the language.
Speech-to-text also mishears sound-alike words; read the command as a shopkeeper meant it \
(e.g. "आता कितना बच्चा है" is "आटा कितना बचा है", a stock question about aata).

Intents:
- ADD_CREDIT: customer took goods on udhaar (credit). "Sharma ji ka 500 udhaar likh do", "वर्मा जी के खाते में 250 चढ़ा दो", "Gupta aunty ne 300 ka saman udhaar liya"
- RECORD_PAYMENT: customer paid back. "Sharma ji ne 200 de diye", "मिश्रा जी से 500 मिल गए", "arre wo Verma ji ne kal ke 200 jama kiye"
- STOCK_IN: stock arrived. "Aata ke 20 packet aaye", "मैगी के 5 डिब्बे स्टॉक में जोड़ो", "Tata namak 10 packet add karo"
- STOCK_OUT: stock went out or was sold. "Maggi ke 3 packet nikle", "दो फॉर्च्यून तेल बिक गए", "Parle-G 12 bech diye"
- MARK_ATTENDANCE: staff came to work. "Ramu aaj aaya hai", "श्याम की हाज़िरी लगा दो", "Ramu present"
- STAFF_ADVANCE: advance money given to staff. "Ramu ko 500 advance diya", "श्याम को एक हज़ार एडवांस दे दिया"
- SALARY_PAID: salary paid to staff. "Shyam ko 4000 salary de di", "रामू की तनख्वाह 5000 दे दी"
- QUERY_CUSTOMER_BALANCE: how much one customer owes. "Sharma ji ka kitna baaki hai?", "वर्मा जी का हिसाब बताओ"
- QUERY_TOTAL_OUTSTANDING: total udhaar across all customers. "Kul kitna udhaar baaki hai?", "सबका मिला के कितना लेना है"
- QUERY_LOW_STOCK: which items are running low. "Kya kya khatam ho raha hai?", "कौन सा सामान कम है"
- QUERY_STOCK_LEVEL: stock of one product. "Aata kitna bacha hai?", "मैगी कितने पैकेट हैं"
- QUERY_STAFF_DUE: one staff member's month: days present, what they earned, what they were given and what \
is still owed (this month unless another month is said). "Ramu ka kitna banta hai?", "श्याम को कितनी तनख्वाह देनी है", \
"Ramu ka pichhle mahine ka hisaab"
- QUERY_STAFF_LIST: how many people work in the shop, or who they are. "Meri dukaan mein kitne staff hain?", \
"दुकान में कितने लोग काम करते हैं", "kaun kaun kaam karta hai yahan", "kitne naukar hain"
- QUERY_ATTENDANCE_TODAY: which staff came (or did not come) to work today. "Aaj kaun kaun aaya?", \
"आज कौन नहीं आया", "aaj sab aaye kya"
- QUERY_CUSTOMER_COUNT: how many customers the shop has, or how many owe money. "Mere kitne customer hain?", \
"कितने ग्राहकों का खाता है", "kitne logon ka hisaab chalta hai"
- QUERY_DEBTOR_LIST: who owes money, or who owes the most. "Kis kis ka udhaar baaki hai?", "सबसे ज़्यादा उधार किसका है"
- QUERY_STOCK_LIST: what items the shop has in stock, or how many. "Stock mein kya kya saamaan hai?", \
"स्टॉक में क्या क्या सामान है", "dukaan mein kitni cheezein hain"
- QUERY_CREDIT_GIVEN: how much udhaar was given in a period, in all or to one customer. "Aaj kitna udhaar diya?", \
"इस हफ्ते शर्मा जी को कितना उधार दिया"
- QUERY_PAYMENTS_RECEIVED: how much money customers paid back in a period, in all or one customer. \
"Aaj kitni vasooli hui?", "पिछले महीने वर्मा जी से कितने पैसे आए", "aaj kitna paisa aaya"
- QUERY_STAFF_PAID: money given to staff (advance and salary) in a period, in all or to one person. \
"Is mahine staff ko kitna diya?", "Shyam ko pichhle mahine kitne paise diye?"
- QUERY_STOCK_MOVEMENT: how much stock came in or went out (was sold) in a period, of all items or one. \
"Aaj kitni Maggi biki?", "इस हफ्ते क्या क्या माल आया", "kal kitna saamaan bika"
- ADD_CUSTOMER: open a khata for a new customer; amount is udhaar they already owe, if said. "Naya customer Rahul \
Verma jodo", "नया ग्राहक मोहन, पुराना उधार 300", "Pandey ji ka naya khata kholo"
- ADD_STAFF: a new person starts working in the shop; amount is their monthly salary. "Naya staff Raju, salary 8000", \
"मोहन को काम पर रखा, तनख्वाह नौ हज़ार महीना", "Naya ladka rakha hai Sonu"
- ADD_PRODUCT: a new item the shop will keep in stock; unit is how it is counted, quantity the stock there is \
now, if said. "Naya saamaan Tata Salt jodo, packet mein", "नया सामान चीनी, किलो में, 50 किलो है", "Surf Excel add karo"
- UNDO_LAST: any request to undo, delete, cancel, reverse or fix an entry, named or not. Voice never changes past entries; KhataFlow only explains how to undo on screen. "Pichhli entry hata do", "गलती हो गई, आखिरी वाला कैंसल करो", "Gupta aunty ke 750 wapas karo"
- UNKNOWN: greetings, or a request none of the intents above can serve even loosely.

Choose the intent by what the shopkeeper wants to record or find out, not by matching words: people ask the \
same thing many ways. Staff are also called log, kaam wale, karmchari, naukar, ladke, workers or helpers; \
customers are also grahak, khatedaar or "jinka khata hai"; udhaar is also baaki, credit, khata or hisaab. \
A question about the shop as a whole ("meri dukaan mein…", "kitne…", "kaun kaun…") with no single name is \
one of the shop-wide query intents, not UNKNOWN.

The same intents in other languages, for example: "শর্মা জির ৫০০ টাকা বাকি লিখে দাও" (ADD_CREDIT, Bengali), \
"ஷர்மா ஜிக்கு ஐநூறு ரூபாய் கடன் எழுது" (ADD_CREDIT, Tamil), "ವರ್ಮಾ ಜೀ 200 ರೂಪಾಯಿ ಕೊಟ್ಟರು" (RECORD_PAYMENT, Kannada), \
"రాము ఈరోజు వచ్చాడు" (MARK_ATTENDANCE, Telugu), "मॅगीचे ५ पाकीट आले" (STOCK_IN, Marathi), \
"ਸ਼ਿਆਮ ਨੂੰ 500 ਐਡਵਾਂਸ ਦਿੱਤਾ" (STAFF_ADVANCE, Punjabi), "આટો કેટલો બચ્યો છે?" (QUERY_STOCK_LEVEL, Gujarati), \
"ആരുടെയൊക്കെ കടം ബാക്കിയുണ്ട്?" (QUERY_DEBTOR_LIST, Malayalam).
QUERY_TOTAL_OUTSTANDING is what is still owed now; QUERY_CREDIT_GIVEN is what was given in a period.

Rules:
- The message may come after a conversation with Khata. Turn only the latest message into one command, and use \
the conversation only to fill in what the latest message leaves out or points back to: the name or item being \
added, "uska", "wahi", or the answer to Khata's last question. After Khata asked "What is Raju's salary?", \
"8000" is ADD_STAFF, Raju, amount 8000; after "Which unit is Maggi sold in?", "packet" is ADD_PRODUCT, Maggi, \
unit packet; after "How much does Sharma ji owe?" was answered, "uska 200 aur likho" is ADD_CREDIT, Sharma ji, 200.
- After Khata reads back a new customer, staff member or product, the shopkeeper may change a detail instead \
of saying yes (read back "Bebo, salary 2000", then "Nahi, salary 3000 kar do" is ADD_STAFF, Bebo, amount 3000; \
"Low stock alert 6 packet pe" adds low_stock_alert 6; "Naam Bebo Sharma hai" renames). The change wins; keep \
every detail it does not change, and the intent unless it is changed too ("ye staff hai, customer nahi").
- A latest message that is a new instruction on its own is parsed on its own, whatever came before.
- ADD_CUSTOMER, ADD_STAFF, ADD_PRODUCT: the name is new, so write it as said, in Latin script and Title Case; \
never swap in a known name.
- party_name / product_name: always Latin script. If it matches a known name below (even loosely, \
by alias, honorific or Devanagari spelling), copy the known name exactly. Otherwise transliterate what was said.
- amount: rupees as a number. quantity: count as a number. Native digits become ASCII digits, and number \
words in any of the languages become digits (ஐநூறு = 500, পাঁচশো = 500, ಸಾವಿರ = 1000). Hindi number words: \
paanch sau = 500, dhai sau = 250, dedh sau = 150, dedh = 1.5, dhai = 2.5, sava = 1.25, saade teen = 3.5, \
ek hazaar = 1000, dedh hazaar = 1500, do hazaar = 2000.
- unit: exactly packet, kg, litre or piece when said (kilo is kg, dabba or pack is packet, nag or pcs is piece), else null.
- date_hint: ISO date (YYYY-MM-DD) only when a past day is said ("kal" means yesterday, "parson" the day \
before yesterday, "pichhle mahine" the first day of last month); null for today ("aaj") or no day.
- period: only for QUERY_CREDIT_GIVEN, QUERY_PAYMENTS_RECEIVED, QUERY_STAFF_PAID, QUERY_STOCK_MOVEMENT and \
QUERY_STAFF_DUE (its month), and only when said: TODAY (aaj), YESTERDAY (kal), THIS_WEEK, LAST_WEEK, THIS_MONTH, LAST_MONTH, or ALL (ab tak, \
kul milake, total). Those intents use period, never date_hint.
- low_stock_alert: ADD_PRODUCT only, the stock level at which to warn that it is running low, when said \
("alert 6 pe", "10 se kam ho to batana").
- Every field not used by the intent is null.
"""


def conversation(transcript: str, history: History) -> str:
    "The user message: just the latest words, or the chat so far and then them."
    if not history:
        return transcript
    lines = [f"Shopkeeper: {said}\nKhata: {reply}" for said, reply in history]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nLatest message: {transcript}"


def build_prompt(context: ShopContext) -> str:
    known = {
        "today": context.today.isoformat(),
        "customers": context.customers,
        "staff": context.staff,
        "products": context.products,
    }
    return f"{INSTRUCTIONS}\nKnown names:\n{json.dumps(known, ensure_ascii=False)}"


class GroqIntentParser:
    "Any OpenAI-compatible chat endpoint; Groq by default."

    def parse(self, transcript: str, context: ShopContext, history: History = ()) -> ParsedCommand:
        messages = [
            {"role": "system", "content": build_prompt(context)},
            {"role": "user", "content": conversation(transcript, history)},
        ]
        strict = True
        for attempt in (1, 2):
            try:
                raw = self._complete(messages, strict=strict)
            except ProviderError as exc:
                # Groq's constrained decoder sometimes gives up mid-word on a name; the same request without
                # it, still validated by Pydantic below, goes through.
                if exc.code != "LLM_JSON_FAILED" or attempt == 2:
                    raise
                strict = False
                messages[0] = {"role": "system", "content": messages[0]["content"] + PLAIN_JSON_HINT}
                continue
            try:
                return ParsedCommand.model_validate_json(raw)
            except ValidationError as exc:
                if attempt == 2:
                    break
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That JSON is invalid: {exc}. Reply with corrected JSON only."},
                ]
        return ParsedCommand(intent=Intent.UNKNOWN)

    def _complete(self, messages: list[dict], strict: bool = True) -> str:
        if not settings.LLM_API_KEY:
            raise ProviderError("LLM_NOT_CONFIGURED", "LLM_API_KEY is empty")
        payload = {
            "model": settings.LLM_MODEL,
            "messages": messages,
            "temperature": 0,
            "reasoning_effort": settings.LLM_REASONING_EFFORT,
            "include_reasoning": False,
        }
        if strict:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "parsed_command", "strict": True, "schema": RESPONSE_SCHEMA},
            }
        waited = False
        while True:
            try:
                response = httpx.post(
                    f"{settings.LLM_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                    json=payload,
                    timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
                )
            except httpx.HTTPError as exc:
                raise ProviderError("LLM_NETWORK", type(exc).__name__) from exc
            if response.status_code != 429:
                break
            # The day's tokens are used up: waiting won't help, but the smaller model has its own daily allowance.
            daily = "(TPD)" in response.text
            if daily and settings.LLM_FALLBACK_MODEL not in ("", payload["model"]):
                log.warning("LLM daily limit on %s; using %s", payload["model"], settings.LLM_FALLBACK_MODEL)
                payload["model"] = settings.LLM_FALLBACK_MODEL
                continue
            if daily or waited:
                break
            # Groq's free tier allows 8,000 tokens a minute (~6 commands); one short wait rides out a burst.
            waited = True
            time.sleep(min(float(response.headers.get("retry-after") or 5), MAX_RATE_LIMIT_WAIT))
        if response.status_code == 400 and "json_validate_failed" in response.text:
            raise ProviderError("LLM_JSON_FAILED", response.text[:200])
        if response.status_code != 200:
            raise ProviderError(f"LLM_HTTP_{response.status_code}", response.text[:200])
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as exc:
            raise ProviderError("LLM_BAD_RESPONSE") from exc


class FakeIntentParser:
    "Returns `result` (set by tests) for any transcript."

    result = ParsedCommand(intent=Intent.UNKNOWN)
    # What the last parse was given, for tests to check.
    seen: tuple[str, list[tuple[str, str]]] = ("", [])

    def parse(self, transcript: str, context: ShopContext, history: History = ()) -> ParsedCommand:
        FakeIntentParser.seen = (transcript, list(history))
        return self.result


def get_intent_parser() -> IntentParser:
    return {"groq": GroqIntentParser, "fake": FakeIntentParser}[settings.LLM_PROVIDER]()
