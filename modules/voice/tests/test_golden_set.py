from pathlib import Path

import yaml

from modules.voice.choices import Intent
from modules.voice.intent import ParsedCommand

GOLDEN = Path(__file__).with_name("golden_commands.yaml")


def test_golden_set_is_valid_and_covers_every_intent():
    cases = yaml.safe_load(GOLDEN.read_text())["cases"]
    parsed = [ParsedCommand(**case["expect"]) for case in cases]
    assert len(parsed) >= 25
    assert {p.intent for p in parsed} == set(Intent)
