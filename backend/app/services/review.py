"""Rolls up individual RuleResult.final_status values into
SessionNoteReview.score/audit_result, and groups results for display
(Failed / Not Applicable / Informational / Passed — same grouping the
frontend's audit detail view already expects). Reads rule metadata
(type/severity/flag) from agent-making's own rules.json rather than
duplicating it into the database — the backend stores only what the
pipeline actually computed.
"""
from app.agent_client import load_rules
from app.config import settings
from app.db.models import RuleResult

_RESULT_TO_GROUP = {
    "fail": "Failed",
    "uncertain": "Failed",
    "not_applicable": "Not Applicable",
    "not_checkable": "Not Applicable",
    "pass": "Passed",
}

# Findings excluded from BOTH the numerator and denominator of `score`:
# not_applicable/not_checkable (nothing to score — the rule genuinely
# doesn't apply or can't be evaluated) and any Informational-type finding
# (a gate/flag, never a scored compliance question — see rules.json's own
# check_type_legend and SN-97153-21's own notes).
_EXCLUDED_FROM_SCORE = {"not_applicable", "not_checkable"}


def load_rules_by_id() -> dict[str, dict]:
    return {r["rule_id"]: r for r in load_rules()}


def finding_group(rule_result: RuleResult, rules_by_id: dict[str, dict]) -> str:
    if rules_by_id.get(rule_result.rule_id, {}).get("type") == "Informational":
        return "Informational"
    return _RESULT_TO_GROUP.get(rule_result.final_status, "Failed")


def compute_score(rule_results: list[RuleResult], rules_by_id: dict[str, dict] | None = None) -> float:
    """passed / (passed + failed) among findings whose check_type is
    deterministic/judgment and whose rules.json type is "Default" —
    "uncertain" counts as failed for this purpose (same bucket the
    Failed/Not Applicable/Informational/Passed grouping above already
    puts it in). 100.0 when there is nothing left to score (every finding
    was not_applicable/not_checkable/Informational) — vacuously "nothing
    wrong found" rather than an undefined/None score.
    """
    rules_by_id = rules_by_id if rules_by_id is not None else load_rules_by_id()
    passed = 0
    failed = 0
    for rr in rule_results:
        rule = rules_by_id.get(rr.rule_id, {})
        if rule.get("type") == "Informational":
            continue
        if rr.final_status in _EXCLUDED_FROM_SCORE:
            continue
        if rr.final_status == "pass":
            passed += 1
        else:  # "fail" or "uncertain"
            failed += 1
    if passed + failed == 0:
        return 100.0
    return round(100.0 * passed / (passed + failed), 2)


def compute_audit_result(score: float, threshold: float | None = None) -> str:
    threshold = threshold if threshold is not None else settings.audit_pass_threshold
    return "pass" if score >= threshold else "fail"


def compute_score_and_audit_result(
    rule_results: list[RuleResult], rules_by_id: dict[str, dict] | None = None,
) -> tuple[float, str]:
    rules_by_id = rules_by_id if rules_by_id is not None else load_rules_by_id()
    score = compute_score(rule_results, rules_by_id)
    return score, compute_audit_result(score)
