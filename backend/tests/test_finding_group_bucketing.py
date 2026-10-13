"""REAL BUG FOUND AND FIXED (2026-10-06): a real passing rule (SN-97153-21,
"Was this NOT a supervised session?" -- check_type=deterministic,
final_status="pass") was showing under "Informational" in the live app
instead of "Passed". Root cause: finding_group() checked rules.json's own
type=="Informational" flag FIRST and returned "Informational"
unconditionally, regardless of the rule's REAL final_status -- conflating
"exclude this rule from the scored percentage" (compute_score's own,
separate, still-correct use of the same flag) with "which display bucket
does a real verdict belong in" (which must be driven by the verdict
itself). Checked against the real rules.json: SN-97153-21 is the ONLY
rule with type=="Informational" today, so no other document was silently
affected by this same mechanism -- but the mechanism itself was wrong in
principle. Uses the REAL rules.json (via load_rules_by_id), not a
synthetic fixture, per the explicit ask to regression-test this exact rule.
"""
from types import SimpleNamespace

from app.services.review import finding_group, load_rules_by_id

_REAL_RULES_BY_ID = load_rules_by_id()


def _rr(rule_id, final_status):
    return SimpleNamespace(rule_id=rule_id, final_status=final_status)


def test_sn_97153_21_is_informational_type_in_the_real_rules_json():
    # Confirms the scope of the bug directly against the real rule set --
    # if this ever stops being true, the regression tests below stop
    # proving anything real and should be revisited.
    assert _REAL_RULES_BY_ID["SN-97153-21"]["type"] == "Informational"


def test_sn_97153_21_passing_buckets_as_passed_not_informational():
    assert finding_group(_rr("SN-97153-21", "pass"), _REAL_RULES_BY_ID) == "Passed"


def test_sn_97153_21_failing_buckets_as_failed_not_informational():
    assert finding_group(_rr("SN-97153-21", "fail"), _REAL_RULES_BY_ID) == "Failed"


def test_sn_97153_21_not_applicable_buckets_as_not_applicable():
    assert finding_group(_rr("SN-97153-21", "not_applicable"), _REAL_RULES_BY_ID) == "Not Applicable"


def test_no_real_rule_still_forces_the_informational_bucket():
    # Scope check: confirms the bug's old mechanism can never resurface
    # for ANY real rule in today's rules.json, not just SN-97153-21 --
    # bucketing is classification-only now, full stop.
    for rule_id, rule in _REAL_RULES_BY_ID.items():
        if not rule.get("active", True):
            continue
        for final_status in ("pass", "fail", "not_applicable", "not_checkable", "uncertain"):
            bucket = finding_group(_rr(rule_id, final_status), _REAL_RULES_BY_ID)
            assert bucket != "Informational", (
                f"{rule_id} with final_status={final_status!r} landed in Informational -- "
                f"bucketing must be driven by classification alone"
            )
