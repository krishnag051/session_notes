"""Unit tests for app/services/review.py's score/threshold computation —
the exact formula: passed / (passed + failed) among findings whose
check_type is deterministic/judgment and whose rules.json type is
"Default" (not_applicable/not_checkable/Informational excluded from both
numerator and denominator). Audit Flags (PASSED/FAILED) is a threshold on
that score, configurable via settings.audit_pass_threshold — NOT a
hardcoded "any single fail = fail" rule.
"""
from types import SimpleNamespace

from app import config as config_module
from app.services.review import compute_audit_result, compute_score, compute_score_and_audit_result

# A tiny, self-contained rules_by_id fixture -- doesn't depend on the real
# rules.json, so these tests stay correct even if the real rule set changes.
_RULES_BY_ID = {
    "R-DEFAULT-1": {"type": "Default"},
    "R-DEFAULT-2": {"type": "Default"},
    "R-DEFAULT-3": {"type": "Default"},
    "R-INFO-1": {"type": "Informational"},
}


def _rr(rule_id, final_status):
    return SimpleNamespace(rule_id=rule_id, final_status=final_status)


def test_score_all_pass_is_100():
    results = [_rr("R-DEFAULT-1", "pass"), _rr("R-DEFAULT-2", "pass")]
    assert compute_score(results, _RULES_BY_ID) == 100.0


def test_score_excludes_not_applicable_and_not_checkable_from_both_sides():
    results = [
        _rr("R-DEFAULT-1", "pass"),
        _rr("R-DEFAULT-2", "not_applicable"),
        _rr("R-DEFAULT-3", "not_checkable"),
    ]
    # Only R-DEFAULT-1 is scored -- 1/1 = 100%, not 1/3.
    assert compute_score(results, _RULES_BY_ID) == 100.0


def test_score_excludes_informational_type_regardless_of_result():
    results = [_rr("R-DEFAULT-1", "pass"), _rr("R-INFO-1", "fail")]
    # R-INFO-1 is Informational -- never scored, even though its own
    # result is "fail".
    assert compute_score(results, _RULES_BY_ID) == 100.0


def test_score_uncertain_counts_as_failed():
    results = [_rr("R-DEFAULT-1", "pass"), _rr("R-DEFAULT-2", "uncertain")]
    assert compute_score(results, _RULES_BY_ID) == 50.0


def test_score_matches_real_screenshot_style_number():
    # 88.46% -- 23 of 26 scored findings passing (matches the style of
    # Krishna's real Brellium screenshot numbers).
    results = [_rr(f"R-{i}", "pass") for i in range(23)] + [_rr(f"R-fail-{i}", "fail") for i in range(3)]
    rules_by_id = {r.rule_id: {"type": "Default"} for r in results}
    assert compute_score(results, rules_by_id) == 88.46


def test_score_is_100_when_nothing_is_scorable():
    results = [_rr("R-INFO-1", "fail")]
    assert compute_score(results, _RULES_BY_ID) == 100.0


def test_audit_result_default_threshold_is_80():
    assert compute_audit_result(79.99) == "fail"
    assert compute_audit_result(80.0) == "pass"  # boundary: >= threshold passes
    assert compute_audit_result(95.0) == "pass"


def test_audit_result_matches_real_screenshot_examples():
    # Krishna's real screenshot: 76% FAILED, 88%/90%/95% PASSED -- all
    # consistent with the default 80% threshold, not a "single fail = fail" rule.
    assert compute_audit_result(76.0) == "fail"
    assert compute_audit_result(88.0) == "pass"
    assert compute_audit_result(90.0) == "pass"
    assert compute_audit_result(95.0) == "pass"


def test_audit_result_threshold_is_configurable_not_hardcoded(monkeypatch):
    monkeypatch.setattr(config_module.settings, "audit_pass_threshold", 90.0)
    assert compute_audit_result(85.0) == "fail"  # would have passed at the default 80% threshold
    assert compute_audit_result(90.0) == "pass"


def test_compute_score_and_audit_result_combined():
    results = [_rr("R-DEFAULT-1", "pass"), _rr("R-DEFAULT-2", "fail")]
    score, audit_result = compute_score_and_audit_result(results, _RULES_BY_ID)
    assert score == 50.0
    assert audit_result == "fail"
