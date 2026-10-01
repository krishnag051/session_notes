"""Phase 4: batch upload auto-triggers a real review per document
(processed one at a time, batch-cumulative spend cap), plus the new
GET .../pdf and GET .../extraction endpoints the split-screen detail page
reads from. All against the mocked model boundary — see conftest.py's
autouse guard and its _maybe_fake_extract_session_note_data helper.
"""
from pathlib import Path

import pypdf

import app.routers.person_documents as person_documents_module
from app.config import settings

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
    def __init__(self, findings, api_calls=2, cost=0.01):
        self.status = "complete"
        self.findings = findings
        self.usage = _FakeUsage(api_calls, cost)
        self.error = None


def _findings():
    return {
        "SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0},
    }


def _mock_pipeline(monkeypatch, *, cost=0.01, extraction_cost=0.0):
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings(), cost=cost))
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {
            "fields": {"session_date": {"value": "09/24/2026", "confidence": "high", "source_quote": "9/24/26"}},
            "api_calls_used": 1,
            "api_cost_usd": extraction_cost,
        },
    )


def test_batch_upload_auto_reviews_every_document_one_at_a_time(client, monkeypatch):
    _mock_pipeline(monkeypatch)
    batch = _upload_batch(client)

    # TestClient runs BackgroundTasks synchronously before returning, so by
    # the time the upload response comes back, every confident document's
    # review has already run to completion (mocked, zero real spend).
    # 3 real people, but Daniel Cazi has TWO documents of his own (97151 +
    # 97156) — 4 confidently-classified documents total, 0 unresolved.
    # 97156 has no rules.json entries at all (see has_applicable_rules) —
    # it reaches "no_applicable_rules" WITHOUT the mocked pipeline ever
    # being called, the other 3 (97151/97153 x2) reach "complete" normally.
    confident_docs = [d for d in batch["documents"] if d["service_code"] is not None]
    assert len(confident_docs) == 4
    for doc in confident_docs:
        resp = client.get(f"/api/person-documents/{doc['id']}/review")
        assert resp.status_code == 200
        body = resp.json()
        if doc["service_code"] == "97156":
            assert body["status"] == "no_applicable_rules"
            assert body["score"] is None
        else:
            assert body["status"] == "complete"
            assert body["score"] == 100.0

    # The UPLOAD response's own embedded review_summary reflects the
    # moment the response was built — BEFORE the background task (which
    # runs strictly after the response is sent) — so it correctly still
    # shows "pending" here; a fresh GET is what shows the finished state.
    assert batch["review_summary"]["pending"] == 4
    refetched = client.get(f"/api/batches/{batch['id']}").json()
    assert refetched["review_summary"]["complete"] == 3
    assert refetched["review_summary"]["no_applicable_rules"] == 1
    assert refetched["review_summary"]["pending"] == 0


def test_extraction_cost_is_folded_into_spend_and_batch_running_total(client, monkeypatch):
    """The actual fix this test file was written for: session_note_
    extraction.py's own cost used to be invisible to spend_usd/the batch
    cap entirely (only its call COUNT was tracked). Mocks a deliberately
    HIGH-cost extraction response (way more expensive than the
    rule-checking call itself) and confirms it now shows up — both on the
    individual review and on the batch's own running total. Mocked, zero
    real spend.
    """
    # A high enough cap that all 4 documents still complete — this test is
    # about cost VISIBILITY, not spend-cap interaction (see the dedicated
    # spend-cap test below for that).
    monkeypatch.setattr(settings, "batch_max_spend_usd", 100.0)
    _mock_pipeline(monkeypatch, cost=0.01, extraction_cost=2.00)
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    review = client.get(f"/api/person-documents/{doc['id']}/review").json()
    assert review["spend_usd"] == 2.01  # $0.01 review_person_document + $2.00 extraction — extraction dominates

    summary = client.get(f"/api/batches/{batch['id']}").json()["review_summary"]
    # Only 3 of the 4 documents actually reach the mocked pipeline — the
    # 4th (97156) has no rules.json entries at all and is skipped before
    # either real call site, contributing $0 (see has_applicable_rules).
    assert summary["complete"] == 3
    assert summary["no_applicable_rules"] == 1
    assert summary["total_spend_usd"] == round(2.01 * 3, 4)


def test_batch_review_summary_totals_spend_and_calls(client, monkeypatch):
    _mock_pipeline(monkeypatch, cost=0.5, extraction_cost=0.1)
    batch = _upload_batch(client)
    resp = client.get(f"/api/batches/{batch['id']}")
    summary = resp.json()["review_summary"]
    # Only 3 of the 4 documents reach the mocked pipeline — the 4th
    # (97156) has no rules.json entries and is skipped for $0 (see
    # has_applicable_rules).
    assert summary["complete"] == 3
    assert summary["no_applicable_rules"] == 1
    assert summary["total_spend_usd"] == 1.8  # 3 documents x ($0.50 review + $0.10 extraction) each
    assert summary["total_api_calls"] == 3 * (2 + 1)  # review_person_document's 2 + extraction's 1, each doc


def test_batch_spend_cap_skips_remaining_documents_once_exceeded(client, monkeypatch):
    monkeypatch.setattr(settings, "batch_max_spend_usd", 1.0)
    # Realistic combined per-document cost is now review + extraction —
    # $0.50 + $0.10 = $0.60/doc, same total as the pre-this-fix numbers
    # this test used to hardcode as a single review-only cost, but now
    # correctly composed from both real call sites.
    _mock_pipeline(monkeypatch, cost=0.5, extraction_cost=0.1)

    batch = _upload_batch(client)
    resp = client.get(f"/api/batches/{batch['id']}")
    summary = resp.json()["review_summary"]

    # Page order (Bergstein, then Cazi's two documents, then Drummer) is
    # deterministic — first two documents complete ($0.60 then $1.20
    # running total), the cap ($1.00) is already exceeded before the
    # third document's turn, so both remaining documents are marked
    # skipped_spend_cap rather than attempted and failed.
    assert summary["complete"] == 2
    assert summary["skipped_spend_cap"] == 2
    assert summary["pending"] == 0
    assert summary["total_spend_usd"] == 1.2


def test_get_person_document_pdf_returns_only_this_documents_own_pages(client, monkeypatch):
    _mock_pipeline(monkeypatch)
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    resp = client.get(f"/api/person-documents/{doc['id']}/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    # BUG FIX proof: this used to default to "attachment" (Starlette's own
    # FileResponse default the moment a filename is passed), which forced
    # a download prompt in the browser instead of letting the PDF render.
    assert resp.headers["content-disposition"].startswith("inline")

    import io
    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) == doc["page_end"] - doc["page_start"] + 1


def test_get_extraction_returns_the_6_field_data_and_data_points(client, monkeypatch):
    _mock_pipeline(monkeypatch)
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    resp = client.get(f"/api/person-documents/{doc['id']}/extraction")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["full_text"] is not None
    assert body["fields"]["session_date"]["value"] == "09/24/2026"
    assert isinstance(body["data_points"], list)  # zero-cost regex extraction, always present once complete


def test_get_extraction_404_when_no_review_row_exists(client, db_session):
    from app.db.models import PersonDocument

    batch = _upload_batch(client)
    unresolved = PersonDocument(
        batch_id=batch["id"], person_id=None, service_code=None, date_of_service=None,
        appendix=None, page_start=1, page_end=1, classification_confidence="unresolved",
    )
    db_session.add(unresolved)
    db_session.commit()

    resp = client.get(f"/api/person-documents/{unresolved.id}/extraction")
    assert resp.status_code == 404
