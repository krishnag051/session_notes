"""Fix Round (2026-10-13), "O(n²) batch processing bug": REAL BUG FOUND AND
FIXED -- every document in a batch used to re-open and re-parse the WHOLE
original batch PDF from disk independently (run_review's own slice, the
sibling-document slice, and the Activity Statement read all did this
separately, per document), with no reuse across documents in the same
batch. For an n-document batch, that scaled as O(n^2): n re-parses of a
file whose own page count also grows with n. Measured directly against the
real 72-page/14-client fixture: 47.75ms per page of real pypdf text
extraction alone -- extrapolated to ~16-17 HOURS of pure, zero-cost, non-
billed overhead for a real 500-document batch, the actual blocker on
supporting that scale (independent of real spend or storage).

Fixed by opening exactly ONE PdfReader per batch run (in run_batch_reviews)
and threading it through run_review -> _load_sibling_documents / slice_pdf_
to_temp_file / extract_activity_statement_rows, instead of each one
re-opening the original file. This test proves the ACTUAL mechanism, not
just the output: the whole-batch PdfReader constructor is called ONCE for
a real multi-document batch run, not once per document -- a call-count
spy, not a timing assertion (timing varies by machine; call count doesn't).

Mocked model boundary throughout, zero real spend -- this is a pure
performance/mechanism fix, not a logic change, so every other existing
test (what a document's review actually CONTAINS) is expected to pass
completely unchanged; this file only adds the NEW assertion that would not
have existed before this fix: how many times the batch's own PDF gets
re-parsed.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module
import app.services.batch_reviews as batch_reviews_module

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


class _FakeUsage:
    def __init__(self, api_calls=2, cost=0.01):
        self.api_calls = api_calls
        self.estimated_cost_usd = cost


class _FakeResult:
    def __init__(self, findings=None):
        self.status = "complete"
        self.findings = findings or {"SN-FAKE-01": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 1, "confidence": 1.0}}
        self.usage = _FakeUsage()
        self.error = None


def _spy_pdf_reader(monkeypatch, real_cls):
    calls = {"count": 0}

    def _spying_reader(*args, **kwargs):
        calls["count"] += 1
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(batch_reviews_module, "PdfReader", _spying_reader)
    monkeypatch.setattr(person_documents_module, "PdfReader", _spying_reader)
    return calls


def test_whole_batch_pdf_is_opened_once_not_once_per_document(client, monkeypatch):
    from pypdf import PdfReader as RealPdfReader

    calls = _spy_pdf_reader(monkeypatch, RealPdfReader)

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.01})
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(person_documents_module, "humanize_findings_batch", lambda texts, **k: [(t, t, {}) for t in texts])

    with open(FIXTURES / "batch_19page_3client.pdf", "rb") as f:
        resp = client.post("/api/batches", files={"file": ("batch_19page_3client.pdf", f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    batch = resp.json()

    confident_docs = [d for d in batch["documents"] if d["service_code"] is not None]
    # This real fixture classifies to Bergstein/97153, Cazi/97151+97156,
    # Drummer/97153 -- 4 confident documents, enough to prove "once per
    # BATCH" apart from "once per document" (which would show 4+, not 1).
    assert len(confident_docs) == 4

    for doc in confident_docs:
        review = client.get(f"/api/person-documents/{doc['id']}/review").json()
        assert review["status"] in ("complete", "no_applicable_rules"), review

    # The real, specific assertion this round's fix is about: ONE PdfReader
    # for the whole batch run, not one per document (would be 4+ before
    # this fix, for the main slice alone -- more once the sibling lookup
    # and Activity Statement read are counted too).
    assert calls["count"] == 1, f"expected the batch PDF to be opened exactly once, got {calls['count']} opens"


def test_manual_single_document_rerun_still_works_without_a_shared_reader(client, monkeypatch):
    """The manual re-run endpoint has no batch to share a reader across --
    confirms run_review's own `shared_pdf_reader=None` fallback (open one
    locally) still produces a normal, correct result on its own, unchanged
    by this round's fix."""
    monkeypatch.setattr(person_documents_module, "extract_session_note_data", lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.01})
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(person_documents_module, "humanize_findings_batch", lambda texts, **k: [(t, t, {}) for t in texts])

    with open(FIXTURES / "batch_19page_3client.pdf", "rb") as f:
        resp = client.post(
            "/api/batches", files={"file": ("batch_19page_3client.pdf", f, "application/pdf")},
            data={"skip_auto_review": "true"},
        )
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "complete"
