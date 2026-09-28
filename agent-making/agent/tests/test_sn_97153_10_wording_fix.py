"""SN-97153-10 wording fix (2026-09-28 real-run follow-up).

IMPORTANT SCOPE NOTE: these tests verify (a) the sharpened wording is
actually present in rules.json and (b) it actually reaches the real
judgment prompt judge.py builds — both plumbing-level, zero-API-cost
checks. They do NOT and cannot prove the real model now answers Bergstein's
and Drummer's documents consistently — that requires a real, billed
Anthropic call, which needs Krishna's separate per-instance approval per
CLAUDE.md's hard rule, distinct from the run that surfaced this bug. The
mocked "does the pipeline converge correctly IF the model agrees" test
below proves the mechanism, not the model's actual behavior.
"""
import json
from pathlib import Path

from ..pipeline import judge as judge_module

RULES_PATH = Path(__file__).parent.parent / "rules" / "rules.json"


def _load_rule(rule_id: str) -> dict:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]
    return next(r for r in rules if r["rule_id"] == rule_id)


def test_sn_97153_10_notes_contain_the_co_sign_clarification():
    rule = _load_rule("SN-97153-10")
    notes_lower = rule["notes"].lower()
    assert "administrative co-sign" in notes_lower
    assert "not itself a claim that the bcba was present" in notes_lower
    assert "session narrative itself" in notes_lower


def test_sn_97153_10_clarification_actually_reaches_the_judgment_prompt():
    """Plumbing check: rules.json's notes field is only useful if
    judge.py's own prompt-builder actually forwards it to the model —
    confirmed here rather than assumed."""
    rule = _load_rule("SN-97153-10")
    content = judge_module._build_prompt(
        [rule], fields={"pages": []}, rendered_images={},
    )
    full_prompt_text = "\n".join(b["text"] for b in content if b.get("type") == "text")
    assert "administrative co-sign" in full_prompt_text.lower()
    assert rule["rule_id"] in full_prompt_text


def test_mechanism_converges_to_a_confident_consistent_pass_when_the_model_agrees(monkeypatch):
    """Mechanism-level only: IF all 5 votes agree 'pass' for a document
    shaped like Bergstein's or Drummer's real note (unchecked BCBA box, 'No'
    to Supervised-by-BCBA, only a signature-page co-sign) -- the standard
    shape a correctly-informed model should now read consistently -- the
    reconciliation logic correctly lands on a confident, consistent pass
    for both, not a split. This is NOT evidence the real model's actual
    behavior changed; only a real, approved API call can show that.
    """
    def _fake_always_pass(judgment_rules, fields, rendered_images, tracker=None, call_reason="call", model_override=None):
        return {
            r["rule_id"]: {
                "result": "pass",
                "evidence": "BCBA box unchecked, 'No' to supervised, narrative never places the BCBA in the session — only a routine signature-page co-sign, which is not a presence claim.",
                "page": 1,
                "confidence": 0.9,
            }
            for r in judgment_rules
        }

    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", _fake_always_pass)
    rule = _load_rule("SN-97153-10")

    for doc_label in ("bergstein-shaped", "drummer-shaped"):
        result = judge_module.run_judgment_checks_majority_vote(
            [rule], fields={"pages": []}, rendered_images={}, n_calls=5, min_agreement=4,
        )
        assert result["SN-97153-10"]["result"] == "pass", doc_label
