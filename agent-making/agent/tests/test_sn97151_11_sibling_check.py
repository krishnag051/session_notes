"""SN-97151-11 ("goal development/updating mentioned in at least one
session note") now checks sibling documents from the same batch/upload
before falling back to not_checkable — unlike SN-97151-10, which stays
genuinely single-document scoped (its own question is "at least one
ASSESSMENT session", and a Parent Training sibling isn't an assessment
document). Exercised directly via fields.py against the REAL Cazi
97151/97156 fixture content, zero model calls.
"""
from pathlib import Path

from ..pipeline import fields as fields_module
from ..pipeline.classify_batch import classify_batch
from ..pipeline.extract import extract_pdf_text

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _cazi_97151_and_97156_text():
    pages = extract_pdf_text(str(FIXTURES / "batch_19page_3client.pdf"))
    result = classify_batch(pages)
    cazi = next(p for p in result["people"] if p["full_name"] == "Daniel Cazi")
    doc_151 = next(d for d in cazi["documents"] if d["service_code"] == "97151")
    doc_156 = next(d for d in cazi["documents"] if d["service_code"] == "97156")

    def _text_for(doc):
        return "\n\n".join(p["text"] for p in pages[doc["page_start"] - 1: doc["page_end"]])

    return _text_for(doc_151), _text_for(doc_156)


def test_real_cazi_97151_alone_has_no_goal_development_evidence_and_is_not_checkable():
    """Confirms the real premise: the 97151 document ALONE genuinely has
    neither the checkbox nor the narrative language — without the sibling
    fix, this is where it used to incorrectly stop."""
    text_151, _ = _cazi_97151_and_97156_text()
    fields = {"full_text": text_151, "sibling_documents": None}
    result = fields_module._check_SN_97151_11(fields)
    assert result["result"] == "not_checkable"


def test_real_cazi_97156_sibling_resolves_the_rule_to_pass():
    text_151, text_156 = _cazi_97151_and_97156_text()
    fields = {
        "full_text": text_151,
        "sibling_documents": [{"service_code": "97156", "full_text": text_156}],
    }
    result = fields_module._check_SN_97151_11(fields)
    assert result["result"] == "pass"
    assert "97156" in result["evidence"]
    assert "same upload" in result["evidence"]


def test_no_sibling_documents_still_falls_back_to_not_checkable():
    text_151, _ = _cazi_97151_and_97156_text()
    fields = {"full_text": text_151, "sibling_documents": None}
    result = fields_module._check_SN_97151_11(fields)
    assert result["result"] == "not_checkable"


def test_a_sibling_with_no_goal_evidence_either_still_falls_back_to_not_checkable():
    text_151, _ = _cazi_97151_and_97156_text()
    fields = {
        "full_text": text_151,
        "sibling_documents": [{"service_code": "97153", "full_text": "Nothing relevant in this sibling at all."}],
    }
    result = fields_module._check_SN_97151_11(fields)
    assert result["result"] == "not_checkable"
