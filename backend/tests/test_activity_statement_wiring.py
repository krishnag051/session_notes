"""The Activity Statement (timesheet) is the SAME batch PDF's own
"Activity Statement - <name>" cover page classify_batch_pdf already scans
as a page-boundary marker — this suite confirms that page number gets
persisted on upload, the new viewer endpoint serves exactly that page, and
run_review actually threads real parsed timesheet rows + this document's
own appendix into review_person_document (mocked model boundary, zero real
spend, matching this suite's standing discipline).
"""
from pathlib import Path

import pypdf

import app.routers.person_documents as person_documents_module

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str = "batch_19page_3client.pdf") -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()


class _FakeUsage:
    def __init__(self, api_calls=2, cost=0.01):
        self.api_calls = api_calls
        self.estimated_cost_usd = cost


class _FakeResult:
    def __init__(self, findings):
        self.status = "complete"
        self.findings = findings
        self.usage = _FakeUsage()
        self.error = None


def _findings():
    return {"SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0}}


def _mock_extraction(monkeypatch):
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0},
    )


def test_upload_populates_activity_statement_page_for_confident_documents(client):
    batch = _upload_batch(client)
    confident_docs = [d for d in batch["documents"] if d["service_code"] is not None]
    assert len(confident_docs) == 4
    for doc in confident_docs:
        # Not on the response model itself (see PersonDocumentOut) — but the
        # review endpoint's own has_activity_statement flag proves the
        # column got populated, without the test reaching into the DB.
        review = client.get(f"/api/person-documents/{doc['id']}/review").json()
        assert review["has_activity_statement"] is True


def test_activity_statement_pdf_endpoint_serves_exactly_one_page(client):
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    resp = client.get(f"/api/person-documents/{doc['id']}/activity-statement-pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")

    import io
    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) == 1
    # The real Bergstein Activity Statement page contents, not the note itself.
    text = reader.pages[0].extract_text()
    assert "Activity Statement" in text
    assert "Joseph Bergstein" in text


def test_activity_statement_pdf_endpoint_404s_for_unresolved_document(client, db_session):
    from app.db.models import PersonDocument

    batch = _upload_batch(client)
    unresolved = PersonDocument(
        batch_id=batch["id"], person_id=None, service_code=None, date_of_service=None,
        appendix=None, page_start=1, page_end=1, classification_confidence="unresolved",
    )
    db_session.add(unresolved)
    db_session.commit()

    resp = client.get(f"/api/person-documents/{unresolved.id}/activity-statement-pdf")
    assert resp.status_code == 404


def test_run_review_threads_real_parsed_timesheet_rows_and_appendix(client, monkeypatch):
    """The actual fix this file exists for: review_person_document must
    receive this document's own real, parsed Activity Statement rows and
    its own appendix — not None, not a placeholder — so the deterministic
    timesheet-alignment checks can resolve for real.
    """
    _mock_extraction(monkeypatch)
    captured = {}

    def _fake_review(pdf_path, service_code, *, prior_extractions=None, model_override=None, timesheet_rows=None, appendix=None, sibling_documents=None, max_spend_usd=None):
        captured["timesheet_rows"] = timesheet_rows
        captured["appendix"] = appendix
        return _FakeResult(_findings())

    monkeypatch.setattr(person_documents_module, "review_person_document", _fake_review)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    assert captured["appendix"] == doc["appendix"]
    assert captured["timesheet_rows"], "expected real parsed rows, not None/empty"
    row_appendices = {r["appendix"] for r in captured["timesheet_rows"]}
    assert doc["appendix"] in row_appendices
