"""Run the real intent LLM over the golden set and print accuracy per intent.

    docker compose exec web python scripts/eval_intents.py

A case passes only if intent, names, amount, quantity, date_hint and period all match (unit is ignored).
Calls are spaced PACE_SECONDS apart to stay under Groq's free-tier 8,000 tokens/minute (~1,230 per call),
so the latencies printed are not inflated by rate-limit waits.
"""

import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

import yaml  # noqa: E402

from modules.voice.intent import GroqIntentParser, ParsedCommand, ShopContext  # noqa: E402

GOLDEN = ROOT / "modules/voice/tests/golden_commands.yaml"
PACE_SECONDS = float(os.environ.get("PACE_SECONDS", "9"))
COMPARED = ("intent", "party_name", "product_name", "amount", "quantity", "date_hint", "period")


def same(expected: ParsedCommand, actual: ParsedCommand) -> bool:
    def norm(value):
        return value.strip().lower() if isinstance(value, str) else value

    return all(norm(getattr(expected, f)) == norm(getattr(actual, f)) for f in COMPARED)


def main() -> None:
    golden = yaml.safe_load(GOLDEN.read_text())
    context = ShopContext(**golden["context"], today=date.fromisoformat(golden["today"]))
    parser = GroqIntentParser()
    totals, passed, latencies = defaultdict(int), defaultdict(int), []
    for index, case in enumerate(golden["cases"]):
        if index:
            time.sleep(PACE_SECONDS)
        expected = ParsedCommand(**case["expect"])
        start = time.perf_counter()
        actual = parser.parse(case["text"], context)
        latencies.append((time.perf_counter() - start) * 1000)
        totals[expected.intent] += 1
        if same(expected, actual):
            passed[expected.intent] += 1
        else:
            got = actual.model_dump(include=set(COMPARED), exclude_none=True, mode="json")
            print(f"MISS {case['text']!r}\n     got {got}")
    print()
    for intent in sorted(totals):
        print(f"{intent:26} {passed[intent]}/{totals[intent]}")
    overall = sum(passed.values()) / sum(totals.values())
    print(f"\nOverall: {sum(passed.values())}/{sum(totals.values())} = {overall:.1%}")
    print(f"Median intent latency: {median(latencies):.0f} ms over {len(latencies)} calls")


if __name__ == "__main__":
    main()
