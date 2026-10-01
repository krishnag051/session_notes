"""Real Activity Statement page parsing — against actual fixture PDFs,
not synthetic text, per this project's own testing convention. Zero
model calls (pure regex/pypdf text extraction).
"""
from pathlib import Path

from ..pipeline.activity_statement import parse_activity_statement_rows, row_for_appendix
from ..pipeline.classify_batch import classify_batch
from ..pipeline.extract import extract_pdf_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _pages(filename: str) -> list[dict]:
    return extract_pdf_text(str(FIXTURES / filename))


def test_bergstein_activity_statement_has_prep_session_and_lunch_rows():
    pages = _pages("batch_19page_3client.pdf")
    result = classify_batch(pages)
    bergstein = next(p for p in result["people"] if p["full_name"] == "Joseph Bergstein")
    cover_text = pages[bergstein["cover_page"] - 1]["text"]

    rows = parse_activity_statement_rows(cover_text)
    assert len(rows) == 3

    prep, session, lunch = rows
    assert prep["service_code"] is None and prep["appendix"] is None  # non-billable

    assert session["service_code"] == "97153"
    assert session["appendix"] == "I"
    assert session["start_time"] == "9:30 AM"
    assert session["end_time"] == "12:30 PM"
    assert session["location"] == "Office"

    assert lunch["service_code"] is None and lunch["appendix"] is None


def test_cazi_activity_statement_has_two_billable_rows_different_appendices():
    """Daniel Cazi has TWO documents (97151 + 97156) billed off the SAME
    cover page — the exact real case the appendix-matching join key
    exists for, as opposed to date/service_code guessing."""
    pages = _pages("batch_19page_3client.pdf")
    result = classify_batch(pages)
    cazi = next(p for p in result["people"] if p["full_name"] == "Daniel Cazi")
    cover_text = pages[cazi["cover_page"] - 1]["text"]

    rows = parse_activity_statement_rows(cover_text)
    assert len(rows) == 2
    assert {r["appendix"] for r in rows} == {"I", "II"}

    row_1 = row_for_appendix(rows, "I")
    assert row_1["service_code"] == "97151"
    assert row_1["start_time"] == "7:00 AM"
    assert row_1["end_time"] == "8:30 AM"
    assert row_1["location"] == "Home"

    row_2 = row_for_appendix(rows, "II")
    assert row_2["service_code"] == "97156"
    assert row_2["start_time"] == "12:00 PM"
    assert row_2["end_time"] == "12:45 PM"
    assert row_2["location"] == "Telehealth Provided in Patient's Home"


def test_row_for_appendix_returns_none_when_no_match_or_no_appendix():
    rows = [{"appendix": "I", "service_code": "97151"}]
    assert row_for_appendix(rows, "III") is None
    assert row_for_appendix(rows, None) is None
