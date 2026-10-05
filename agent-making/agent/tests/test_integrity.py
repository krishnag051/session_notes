"""Fix Round (2026-10-05), "humanizer writing quality": real root cause of
the reflexive "...though a specific page couldn't be confirmed, but the
result is accurate"-shaped hedging Krishna flagged by real side-by-side
comparison against a reference tool -- PAGE_UNAVAILABLE_NOTE used to be
appended to EVERY page-unresolved finding, pass included. Tests this
directly against _run_page_recovery_pass, no mocked model call needed --
the behavior under test is pure post-processing over an already-decided
`results` dict.
"""
from ..pipeline import integrity as integrity_module


def _unresolved_finding(result: str) -> dict:
    return {"result": result, "evidence": f"evidence for {result}", "page": None, "confidence": 0.9, "page_unresolved": True}


def _run_with_no_more_retries(results: dict) -> dict:
    # max_page_retries=0 -- every unresolved_id falls straight through to
    # the "still unresolved after retries" branch this round's fix lives
    # in, with no judge.py call in between (judgment_rules/fields/
    # rendered_images are never touched on that path).
    return integrity_module._run_page_recovery_pass(
        judgment_rules=[], fields={}, rendered_images={}, results=results, max_page_retries=0,
    )


def test_pass_finding_gets_no_hedge_note_when_page_stays_unresolved():
    results = {"R-1": _unresolved_finding("pass")}
    out = _run_with_no_more_retries(results)
    assert out["R-1"]["evidence"] == "evidence for pass"


def test_fail_finding_gets_a_plain_note_when_page_stays_unresolved():
    results = {"R-1": _unresolved_finding("fail")}
    out = _run_with_no_more_retries(results)
    assert out["R-1"]["evidence"] == "evidence for fail No specific page could be confirmed for this finding."


def test_uncertain_finding_gets_a_plain_note_when_page_stays_unresolved():
    results = {"R-1": _unresolved_finding("uncertain")}
    out = _run_with_no_more_retries(results)
    assert out["R-1"]["evidence"] == "evidence for uncertain No specific page could be confirmed for this finding."


def test_page_unavailable_note_no_longer_contains_the_old_apologetic_phrasing():
    # The exact phrase Krishna's side-by-side comparison flagged as reading
    # like an apology rather than a finding -- confirms it's actually gone,
    # not just reworded elsewhere.
    assert "still accurate" not in integrity_module.PAGE_UNAVAILABLE_NOTE
    assert "could be confirmed" in integrity_module.PAGE_UNAVAILABLE_NOTE


def test_page_unresolved_flag_is_cleared_regardless_of_result():
    results = {"R-1": _unresolved_finding("pass"), "R-2": _unresolved_finding("fail")}
    out = _run_with_no_more_retries(results)
    assert "page_unresolved" not in out["R-1"]
    assert "page_unresolved" not in out["R-2"]
