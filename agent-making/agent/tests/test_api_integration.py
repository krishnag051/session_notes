"""End-to-end against real sample PDFs, model calls MOCKED. The real-API
version comes later, explicitly gated per CLAUDE.md's hard rule.
"""
from pathlib import Path

from ..pipeline import api as api_module
from ..pipeline import judge as judge_module

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_judgment(judgment_rules, fields, rendered_images, tracker=None, call_reason="call", model_override=None):
    """Every judgment rule passes, real page 1, so run_judgment_with_integrity_check
    (which calls run_judgment_checks_majority_vote, itself n_calls x this
    fake) never needs a retry."""
    return {
        r["rule_id"]: {"result": "pass", "evidence": "mocked judgment pass.", "page": 1, "confidence": 0.9}
        for r in judgment_rules
    }


def test_review_person_document_97153_real_fixture_mocked_judgment(monkeypatch):
    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", _fake_judgment)

    result = api_module.review_person_document(
        str(FIXTURES / "single_doc_97153_bergstein.pdf"), "97153", prior_extractions=None,
    )

    assert result.status == "complete"
    assert result.error is None
    assert result.service_code == "97153"
    assert result.usage.api_calls == 0  # every real call site was mocked/short-circuited

    # A real deterministic finding actually computed from this real fixture:
    # Bergstein's note is signed within 2 days (real fixture text confirmed
    # during Phase 1's own fixture inspection).
    assert result.findings["SN-97153-16"]["result"] == "pass"

    # A needs_history rule with no prior_extractions passed in must resolve
    # to not_checkable WITHOUT ever reaching the (mocked) judgment layer —
    # confirmed by its evidence text, which only history_comparison.py's
    # own short-circuit produces.
    assert result.findings["SN-97153-02"]["result"] == "not_checkable"
    assert "no prior session data" in result.findings["SN-97153-02"]["evidence"].lower()

    # Every active 97153 rule must appear in findings exactly once.
    from ..pipeline.api import _load_rules
    active_97153_rule_ids = {r["rule_id"] for r in _load_rules() if r["service_code"] == "97153" and r["active"]}
    assert set(result.findings) == active_97153_rule_ids


def test_review_person_document_97151_real_fixture_mocked_judgment(monkeypatch):
    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", _fake_judgment)

    result = api_module.review_person_document(
        str(FIXTURES / "single_doc_97151_cazi_appendix1.pdf"), "97151", prior_extractions=None,
    )

    assert result.status == "complete"
    assert result.service_code == "97151"
    # A real deterministic finding: Cazi's assessment insurance is Healthfirst
    # (5h threshold) and the real session duration is well under it.
    assert result.findings["SN-97151-13"]["result"] == "pass"


def test_review_person_document_needs_history_with_similar_prior_extraction(monkeypatch):
    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", _fake_judgment)

    from ..pipeline.extract import extract_pdf_text
    pages = extract_pdf_text(str(FIXTURES / "single_doc_97153_bergstein.pdf"))
    current_full_text = "\n\n".join(p["text"] for p in pages)

    result = api_module.review_person_document(
        str(FIXTURES / "single_doc_97153_bergstein.pdf"), "97153",
        prior_extractions=[{"date_of_service": "2026-09-17", "full_text": current_full_text}],
    )

    # Byte-identical "prior" text -> 100% similarity -> flagged, but still
    # routed through the (mocked) judgment layer for a real read, not
    # auto-failed by the deterministic signal alone.
    assert result.findings["SN-97153-02"]["result"] == "pass"  # from the mocked judgment call
    assert result.status == "complete"


def test_review_person_document_returns_structured_error_for_missing_file():
    result = api_module.review_person_document("/no/such/file.pdf", "97153", prior_extractions=None)
    assert result.status == "error"
    assert result.error is not None
