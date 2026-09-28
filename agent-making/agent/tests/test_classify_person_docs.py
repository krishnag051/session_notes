from pathlib import Path

import pytest

from ..pipeline.classify_person_docs import ClassificationAmbiguous, classify_person_docs
from ..pipeline.extract import extract_pdf_text

FIXTURES = Path(__file__).parent / "fixtures"


def _pages_in_range(fixture: Path, start: int, end: int) -> list[dict]:
    pages = extract_pdf_text(str(fixture))
    return [p for p in pages if start <= p["page_number"] <= end]


def test_single_appendix_document_97153():
    # Joseph Bergstein, pages 2-6 of batch_72page_14client.pdf: one
    # 97153 note tagged Appendix I throughout, 5 pages.
    pages = _pages_in_range(FIXTURES / "batch_72page_14client.pdf", 2, 6)
    docs = classify_person_docs(pages)
    assert docs == [{
        "appendix": "I",
        "service_code": "97153",
        "date_of_service": "2026-09-24",
        "page_start": 2,
        "page_end": 6,
    }]


def test_two_appendix_split_daniel_cazi_97151_and_97156():
    # Daniel Cazi, pages 8-12: Appendix I (97151 assessment, 2 pages) then
    # Appendix II (97156 parent training, 3 pages) — the real multi-service-code
    # case this whole module exists to handle correctly.
    pages = _pages_in_range(FIXTURES / "batch_72page_14client.pdf", 8, 12)
    docs = classify_person_docs(pages)
    assert docs == [
        {
            "appendix": "I",
            "service_code": "97151",
            "date_of_service": "2026-09-24",
            "page_start": 8,
            "page_end": 9,
        },
        {
            "appendix": "II",
            "service_code": "97156",
            "date_of_service": "2026-09-24",
            "page_start": 10,
            "page_end": 12,
        },
    ]


def test_three_appendix_split_holland_daylyn_1client():
    pages = _pages_in_range(FIXTURES / "holland_daylyn_1client.pdf", 2, 7)
    docs = classify_person_docs(pages)
    assert [d["appendix"] for d in docs] == ["I", "II", "III"]
    assert [d["service_code"] for d in docs] == ["97151", "97151", "97151"]
    assert [d["date_of_service"] for d in docs] == ["2026-09-04", "2026-09-05", "2026-09-06"]
    assert docs[0]["page_start"] == 2 and docs[0]["page_end"] == 3
    assert docs[1]["page_start"] == 4 and docs[1]["page_end"] == 5
    assert docs[2]["page_start"] == 6 and docs[2]["page_end"] == 7


def test_empty_range_returns_no_documents():
    assert classify_person_docs([]) == []


def test_footer_page_count_mismatch_raises_ambiguous():
    # Real 5-page document with the last page dropped -- the footer's own
    # "Page X of 5" no longer matches the 4 pages actually present, so this
    # must raise rather than silently return a 4-page document as if it
    # were the real, complete one.
    pages = _pages_in_range(FIXTURES / "batch_72page_14client.pdf", 2, 5)
    with pytest.raises(ClassificationAmbiguous, match="claims 5 page"):
        classify_person_docs(pages)


def test_corrupted_footer_mid_group_raises_ambiguous_rather_than_a_wrong_split():
    # One page's own footer is unparseable, sandwiched between pages that
    # are clearly still part of the same appendix-I document — it must
    # carry forward as part of that group (never silently truncate it into
    # a shorter, wrong document) and then raise on that specific page,
    # rather than blending into the group as if nothing were wrong.
    pages = _pages_in_range(FIXTURES / "batch_72page_14client.pdf", 2, 6)
    pages[2]["text"] = pages[2]["text"].replace("I - Page 3 of 5", "")
    with pytest.raises(ClassificationAmbiguous, match="without a parseable"):
        classify_person_docs(pages)
