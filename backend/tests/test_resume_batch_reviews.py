"""The real resume feature (2026-10-15): a batch whose run died or hit its
own spend cap leaves some documents at status="pending" forever without
this. Tests the three real behaviors that matter:
- resuming only reviews the still-pending documents, never the completed ones
- a double-trigger (a run already active) is rejected, not duplicated
- an orphaned "processing" row (left behind by a killed process) is
  recovered automatically — this is the code path app/main.py's own
  startup handler calls, tested directly here rather than via a real
  process restart.

All against the mocked model boundary — see conftest.py's autouse guard.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module
from app.db.models import AuditLog, PersonDocument, ReviewCache, SessionNoteBatch, SessionNoteReview
from app.services.batch_reviews import recover_orphaned_processing_reviews

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


def _mock_pipeline(monkeypatch, calls: list | None = None):
    def _fake_review(pdf_path, service_code, **kwargs):
        if calls is not None:
            calls.append(service_code)
        return _FakeResult(_findings())

    monkeypatch.setattr(person_documents_module, "review_person_document", _fake_review)
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {
            "fields": {"session_date": {"value": "09/24/2026", "confidence": "high", "source_quote": "9/24/26"}},
            "api_calls_used": 1,
            "api_cost_usd": 0.0,
        },
    )


def test_resume_only_reviews_the_still_pending_documents(client, monkeypatch, db_session):
    """Upload fully auto-reviews (TestClient runs BackgroundTasks
    synchronously). Manually puts 2 of the 3 real "complete" documents back
    to "pending" + clears review_cache — simulating exactly the real state
    a killed-mid-batch process leaves behind, and forcing a genuine cache
    MISS so resume must actually call the real (mocked) pipeline again
    rather than legitimately short-circuiting via its own prior cache
    write (a real behavior this project relies on, but not what THIS test
    is isolating). Only those 2 reset documents may be re-reviewed; the
    untouched one keeps its original review row, byte-for-byte.
    """
    calls: list = []
    _mock_pipeline(monkeypatch, calls=calls)
    batch = _upload_batch(client)
    batch_id = batch["id"]

    complete_docs = [d for d in batch["documents"] if d["service_code"] in ("97151", "97153")]
    assert len(complete_docs) == 3
    calls.clear()  # only count calls made by the RESUME below, not the initial upload

    reviews = db_session.query(SessionNoteReview).join(PersonDocument).filter(
        PersonDocument.batch_id == batch_id, PersonDocument.service_code.in_(["97151", "97153"]),
    ).all()
    assert len(reviews) == 3
    to_reset = reviews[:2]
    untouched_review_id = reviews[2].id
    untouched_before = {
        "status": reviews[2].status, "score": reviews[2].score, "spend_usd": reviews[2].spend_usd,
        "api_calls_used": reviews[2].api_calls_used,
    }
    for r in to_reset:
        r.status = "pending"
    db_session.query(ReviewCache).delete()  # force a real cache MISS for the reset documents
    db_session.commit()

    resp = client.post(f"/api/batches/{batch_id}/resume-reviews", json={})
    assert resp.status_code == 200, resp.text

    assert len(calls) == 2, f"expected exactly 2 real review calls (the reset documents only), got {calls}"

    db_session.expire_all()
    for r in to_reset:
        resumed = db_session.get(SessionNoteReview, r.id)
        assert resumed.status == "complete", "a reset document must actually get reviewed by resume"

    untouched = db_session.get(SessionNoteReview, untouched_review_id)
    assert untouched.status == untouched_before["status"] == "complete"
    assert untouched.score == untouched_before["score"]
    assert untouched.spend_usd == untouched_before["spend_usd"]
    assert untouched.api_calls_used == untouched_before["api_calls_used"]

    refreshed_batch = db_session.get(SessionNoteBatch, batch_id)
    assert refreshed_batch.reviews_active is False, "must release the claim once the resumed run finishes"


def test_resume_rejects_a_double_trigger_on_an_already_active_batch(client, monkeypatch, db_session):
    """A batch whose reviews_active is already True (a run is genuinely in
    progress) must reject a second resume call with 409 — never start an
    overlapping run_batch_reviews alongside the first.
    """
    calls: list = []
    _mock_pipeline(monkeypatch, calls=calls)
    batch = _upload_batch(client)
    batch_id = batch["id"]

    # Force one document back to pending (so resume would otherwise have
    # real work to do) and simulate "a run is already active" directly.
    review = db_session.query(SessionNoteReview).join(PersonDocument).filter(
        PersonDocument.batch_id == batch_id, PersonDocument.service_code == "97153",
    ).first()
    review.status = "pending"
    live_batch = db_session.get(SessionNoteBatch, batch_id)
    live_batch.reviews_active = True
    db_session.commit()
    calls.clear()

    resp = client.post(f"/api/batches/{batch_id}/resume-reviews", json={})
    assert resp.status_code == 409, resp.text
    assert len(calls) == 0, "a rejected double-trigger must never start a real review"


def test_resume_rejects_a_batch_with_nothing_pending(client, monkeypatch):
    _mock_pipeline(monkeypatch)
    batch = _upload_batch(client)  # fully completes synchronously, nothing left pending

    resp = client.post(f"/api/batches/{batch['id']}/resume-reviews", json={})
    assert resp.status_code == 409, resp.text


def test_orphaned_processing_review_recovered_automatically(db_session):
    """Direct test of the function app/main.py's own startup handler calls
    — a "processing" review whose batch has reviews_active=False (no
    genuinely active run claims it) is the exact signature a killed
    process leaves behind, and must be reset to "pending" with a real
    audit_log entry. A control row whose batch IS reviews_active=True
    (a real, live run genuinely still owns it) must be left untouched.
    """
    orphaned_batch = SessionNoteBatch(
        original_pdf_path="/tmp/orphaned.pdf", classification_result={}, reviews_active=False,
    )
    live_batch = SessionNoteBatch(
        original_pdf_path="/tmp/live.pdf", classification_result={}, reviews_active=True,
    )
    db_session.add_all([orphaned_batch, live_batch])
    db_session.flush()

    orphaned_doc = PersonDocument(
        batch_id=orphaned_batch.id, person_id=None, service_code="97153",
        page_start=1, page_end=1, classification_confidence="confident",
    )
    live_doc = PersonDocument(
        batch_id=live_batch.id, person_id=None, service_code="97153",
        page_start=1, page_end=1, classification_confidence="confident",
    )
    db_session.add_all([orphaned_doc, live_doc])
    db_session.flush()

    orphaned_review = SessionNoteReview(person_document_id=orphaned_doc.id, status="processing")
    live_review = SessionNoteReview(person_document_id=live_doc.id, status="processing")
    db_session.add_all([orphaned_review, live_review])
    db_session.commit()

    recovered_count = recover_orphaned_processing_reviews(db_session)
    assert recovered_count == 1

    db_session.expire_all()
    assert db_session.get(SessionNoteReview, orphaned_review.id).status == "pending"
    assert db_session.get(SessionNoteReview, live_review.id).status == "processing", (
        "a review whose batch genuinely still has an active run must never be touched"
    )

    audit_entries = db_session.query(AuditLog).filter(
        AuditLog.entity_type == "session_note_review", AuditLog.entity_id == orphaned_review.id,
    ).all()
    assert len(audit_entries) == 1
    assert audit_entries[0].field == "status"
    assert audit_entries[0].old_value == "processing"
    assert audit_entries[0].new_value == "pending"
