"""Priority 3 of a real manual audit round: SN-97153-07 (and its SN-97151-05/
SN-97155-12 siblings, same rule family) used to only forbid one direction
("peer mentioned in narrative but not checked") — real batch data showed
the SAME fact pattern (peer checked as attending, never mentioned in the
narrative) getting PASS, FAIL, and UNCERTAIN across different documents,
because the rule's own wording never said that direction was a violation
at all. Decided (with the user): BOTH directions are real violations.
This is a regression guard on the rules.json wording itself, not judge
behavior (which needs a real model call to verify) — confirms the
decision is actually written into what the judge model reads, not just
decided in conversation and forgotten.
"""
import json
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "rules.json"


def _rule(rule_id: str) -> dict:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]
    return next(r for r in rules if r["rule_id"] == rule_id)


def test_sn_97153_07_description_and_notes_cover_both_directions():
    rule = _rule("SN-97153-07")
    assert "both directions" in rule["description"]
    assert "both directions" in rule["notes"].lower() or "fail both directions" in rule["notes"].lower()


def test_sn_97151_05_description_and_notes_cover_both_directions():
    rule = _rule("SN-97151-05")
    assert "both directions" in rule["description"]
    assert "both directions" in rule["notes"].lower() or "fail both directions" in rule["notes"].lower()


def test_sn_97155_12_description_and_notes_cover_both_directions():
    rule = _rule("SN-97155-12")
    assert "both directions" in rule["description"]
    assert "both directions" in rule["notes"].lower() or "fail both directions" in rule["notes"].lower()


def test_rules_json_is_still_valid_and_same_rule_count():
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    assert len(data["rules"]) == 65
