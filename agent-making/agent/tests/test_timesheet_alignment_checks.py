"""SN-*-04/SN-*-09 (align-with-timesheet / 15-minute-break) — now real
deterministic checks against this batch's own Activity Statement data,
not a standing not_checkable stub. Exercised directly via
fields.run_deterministic_checks (zero-cost, no model call, no mocking
needed at all) against the REAL Bergstein/Cazi fixture data end-to-end.
"""
from pathlib import Path

from ..pipeline import fields as fields_module
from ..pipeline.activity_statement import parse_activity_statement_rows
from ..pipeline.classify_batch import classify_batch
from ..pipeline.extract import extract_pdf_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_rules_for(service_code: str) -> list[dict]:
    import json
    rules = json.loads((Path(__file__).resolve().parent.parent / "rules" / "rules.json").read_text())["rules"]
    return [r for r in rules if r["service_code"] == service_code and r["active"]]


def _bergstein_context():
    pages = extract_pdf_text(str(FIXTURES / "batch_19page_3client.pdf"))
    result = classify_batch(pages)
    bergstein = next(p for p in result["people"] if p["full_name"] == "Joseph Bergstein")
    doc = bergstein["documents"][0]
    cover_text = pages[bergstein["cover_page"] - 1]["text"]
    rows = parse_activity_statement_rows(cover_text)
    content_pages = pages[doc["page_start"] - 1: doc["page_end"]]
    full_text = "\n\n".join(p["text"] for p in content_pages)
    return doc, rows, full_text


def test_bergstein_real_document_passes_timesheet_alignment_with_real_data():
    doc, rows, full_text = _bergstein_context()
    fields = {
        "pages": [], "full_text": full_text, "service_code": "97153",
        "timesheet_rows": rows, "appendix": doc["appendix"],
    }
    det_results, escalated = fields_module.run_deterministic_checks(_load_rules_for("97153"), fields)
    assert "SN-97153-04" not in [r["rule_id"] for r in escalated]
    assert det_results["SN-97153-04"]["result"] == "pass"


def test_bergstein_real_document_15min_break_is_not_applicable_with_one_billable_session():
    doc, rows, full_text = _bergstein_context()
    fields = {
        "pages": [], "full_text": full_text, "service_code": "97153",
        "timesheet_rows": rows, "appendix": doc["appendix"],
    }
    det_results, _ = fields_module.run_deterministic_checks(_load_rules_for("97153"), fields)
    assert det_results["SN-97153-09"]["result"] == "not_applicable"


def test_timesheet_alignment_fails_on_a_real_mismatch():
    """Regression coverage for the FAIL path — deliberately feeds a
    timesheet row that doesn't match this document's real extracted
    fields, using the same real Bergstein full_text."""
    _, rows, full_text = _bergstein_context()
    mismatched_rows = [dict(r, start_time="11:00 AM") for r in rows if r["appendix"] == "I"]
    fields = {
        "pages": [], "full_text": full_text, "service_code": "97153",
        "timesheet_rows": mismatched_rows, "appendix": "I",
    }
    det_results, _ = fields_module.run_deterministic_checks(_load_rules_for("97153"), fields)
    assert det_results["SN-97153-04"]["result"] == "fail"
    assert "start time" in det_results["SN-97153-04"]["evidence"]


def test_timesheet_alignment_is_not_checkable_with_no_timesheet_data():
    _, _, full_text = _bergstein_context()
    fields = {"pages": [], "full_text": full_text, "service_code": "97153", "timesheet_rows": None, "appendix": "I"}
    det_results, _ = fields_module.run_deterministic_checks(_load_rules_for("97153"), fields)
    assert det_results["SN-97153-04"]["result"] == "not_checkable"


def test_cazi_two_billable_sessions_different_locations_far_apart_passes_break_check():
    pages = extract_pdf_text(str(FIXTURES / "batch_19page_3client.pdf"))
    result = classify_batch(pages)
    cazi = next(p for p in result["people"] if p["full_name"] == "Daniel Cazi")
    cover_text = pages[cazi["cover_page"] - 1]["text"]
    rows = parse_activity_statement_rows(cover_text)
    # Real Cazi data: 7:00-8:30 AM (Home) then 12:00-12:45 PM (Telehealth) —
    # over 3 hours apart, easily clears the 15-minute minimum.
    fields = {"pages": [], "full_text": "", "service_code": "97151", "timesheet_rows": rows, "appendix": "I"}
    det_results, _ = fields_module.run_deterministic_checks(_load_rules_for("97151"), fields)
    # SN-97151 has no 15-min-break rule (that's 97153/97155-only) — confirm
    # via the 97153 rule set instead, reusing the same real Cazi rows.
    det_results_153, _ = fields_module.run_deterministic_checks(_load_rules_for("97153"), fields)
    assert det_results_153["SN-97153-09"]["result"] == "pass"
