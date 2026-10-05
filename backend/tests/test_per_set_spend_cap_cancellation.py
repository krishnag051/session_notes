"""Fix Round (2026-10-05), "per-classified-set spend cap": a $1.40 hard
ceiling on one classified set's (one person's one session note + its
matched activity/supporting data -- one PersonDocument/SessionNoteReview,
the SAME unit app/config.py's own per_document_hard_cap_usd already
protected, RECONCILED to this one number rather than left as a second,
differently-named $2.00 cap doing the identical job). Mocked model
boundary throughout, zero real spend.

Covers the three things this round explicitly asked for:
1. A set that would exceed $1.40 is stopped exactly at the cap and marked
   status="cancelled_spend_cap" -- never silently finished, never
   overcharged past it, never conflated with status="failed".
2. Other sets in the SAME batch are completely unaffected by one set
   hitting its own cap.
3. A document that fails classification entirely (no service_code at all)
   never reaches a billed call -- confirms the EXISTING create_pending_
   reviews_for_batch guard, which already skips it before any review row
   is even created.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module
import app.services.batch_reviews as batch_reviews_module
from app.config import settings
from app.db.models import PersonDocument, SessionNoteReview
from agent.pipeline.model_provider import ModelCallCapExceeded

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str = "batch_19page_3client.pdf", skip_auto_review: bool = True) -> dict:
    data = {"skip_auto_review": "true"} if skip_auto_review else {}
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")}, data=data)
    assert resp.status_code == 201, resp.text
    return resp.json()


class _FakeUsage:
    def __init__(self, api_calls=2, cost=0.01):
        self.api_calls = api_calls
        self.estimated_cost_usd = cost


class _FakeResult:
    def __init__(self, findings=None, cost=0.01):
        self.status = "complete"
        self.findings = findings or {}
        self.usage = _FakeUsage(cost=cost)
        self.error = None


def test_the_cap_is_reconciled_to_one_number_not_two():
    """The explicit reconciliation this round asked for -- no second,
    differently-named setting doing the same job."""
    assert settings.per_document_hard_cap_usd == 1.40


def test_a_set_that_would_exceed_the_cap_is_stopped_and_marked_cancelled(client, monkeypatch):
    """Simulates extraction itself hitting the cap mid-call (the real
    shape: model_provider.CallTracker.check_before_call() raises
    ModelCallCapExceeded before the real HTTP call is ever made) --
    review_person_document must never even be reached, and the review
    must land on a distinct, clear "cancelled_spend_cap" status, never
    "failed" (that status means a bug/crash, which this explicitly isn't).
    """
    review_person_document_called = {"count": 0}

    def _extraction_hits_the_cap(*a, **k):
        raise ModelCallCapExceeded("Refusing call #1: already spent $1.40, at or over the $1.40 cap.")

    def _should_never_be_called(*a, **k):
        review_person_document_called["count"] += 1
        return _FakeResult()

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _extraction_hits_the_cap)
    monkeypatch.setattr(person_documents_module, "review_person_document", _should_never_be_called)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["status"] == "cancelled_spend_cap"
    assert body["status"] != "failed"
    assert "cap" in (body["error_message"] or "").lower()
    assert review_person_document_called["count"] == 0, "review_person_document must never be reached once the cap already tripped"


def test_a_cap_trip_is_never_retried(client, monkeypatch):
    """The retry loop must not burn attempts/backoff time retrying a
    guaranteed-repeat outcome -- confirms extract_session_note_data is
    called exactly ONCE, not review_retry_attempts+1 times."""
    call_count = {"n": 0}

    def _always_hits_the_cap(*a, **k):
        call_count["n"] += 1
        raise ModelCallCapExceeded("cap exceeded")

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _always_hits_the_cap)
    monkeypatch.setattr(settings, "review_retry_backoff_seconds", 0.0)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "cancelled_spend_cap"
    assert call_count["n"] == 1, f"expected exactly 1 attempt, got {call_count['n']}"


def test_review_person_documents_own_structured_cap_exceeded_error_also_lands_on_cancelled(client, monkeypatch):
    """The OTHER real path: review_person_document itself catches the cap
    exception internally (agent-making's own api.py) and returns a
    structured status="error"/error_type="cap_exceeded" instead of
    raising -- must ALSO land on "cancelled_spend_cap", not "failed"."""
    class _CapExceededResult:
        status = "error"
        error_type = "cap_exceeded"
        error = "ApiSpendCapExceeded: already spent $1.40, at or over the $1.40 cap."

        class usage:
            api_calls = 3
            estimated_cost_usd = 1.40

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0})
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _CapExceededResult())

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "cancelled_spend_cap"
    assert "cap" in body["error_message"].lower()


def test_other_sets_in_the_same_batch_are_unaffected_by_one_sets_cap_trip(client, monkeypatch):
    """One document's own cap trip must never affect ANY other document
    in the same batch -- each is an independent run_review call."""
    calls_by_path = {}

    def _extraction_by_path(pdf_path, **k):
        calls_by_path[pdf_path] = calls_by_path.get(pdf_path, 0) + 1
        # Deterministic: the FIRST distinct path seen hits the cap, every
        # other path succeeds normally -- proves failure isolation without
        # depending on which specific document happens to be "first" by name.
        if len(calls_by_path) == 1:
            raise ModelCallCapExceeded("cap exceeded")
        return {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.01}

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _extraction_by_path)
    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult({"SN-FAKE-01": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 1, "confidence": 1.0}}),
    )
    monkeypatch.setattr(person_documents_module, "humanize_findings_batch", lambda texts, **k: [(t, t, {}) for t in texts])

    batch = _upload_batch(client, skip_auto_review=False)  # auto-review runs for real (mocked) on upload
    confident_docs = [d for d in batch["documents"] if d["service_code"] is not None]
    assert len(confident_docs) >= 2, "need at least 2 real documents in this batch to prove isolation"

    statuses = []
    for doc in confident_docs:
        review = client.get(f"/api/person-documents/{doc['id']}/review").json()
        statuses.append(review["status"])

    assert "cancelled_spend_cap" in statuses, "expected exactly one document to have hit the cap"
    assert statuses.count("cancelled_spend_cap") == 1
    assert "complete" in statuses, "every OTHER document must have completed normally, unaffected"


def test_an_unresolved_classification_document_never_reaches_a_billed_call(client, monkeypatch, db_session):
    """Real requirement: a document classification couldn't identify any
    valid session-note type for at all must be cancelled before ANY
    billed processing -- confirms the EXISTING guard in
    create_pending_reviews_for_batch (service_code is None -> no
    SessionNoteReview row is ever created, so no billed call is even
    possible), with an explicit assertion that no real call site was ever
    invoked for it.
    """
    extraction_called = {"count": 0}
    review_called = {"count": 0}

    def _track_extraction(*a, **k):
        extraction_called["count"] += 1
        return {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.01}

    def _track_review(*a, **k):
        review_called["count"] += 1
        return _FakeResult()

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _track_extraction)
    monkeypatch.setattr(person_documents_module, "review_person_document", _track_review)

    # skip_auto_review=True: this test cares only about the SYNTHETIC
    # unresolved document inserted below, not this real fixture's own
    # (confidently-classified) documents, which would otherwise also
    # auto-review and pollute the call counters this test asserts on.
    batch = _upload_batch(client, skip_auto_review=True)
    # The real fixture (batch_19page_3client.pdf) classifies everything
    # confidently -- insert a genuinely unresolved document directly, same
    # approach test_review_unresolved_document_returns_409 already uses.
    unresolved = PersonDocument(
        batch_id=batch["id"], person_id=None, service_code=None, date_of_service=None,
        appendix=None, page_start=1, page_end=1, classification_confidence="unresolved",
        classification_note="synthetic, for this test only",
    )
    db_session.add(unresolved)
    db_session.commit()

    from app.services.batch_reviews import create_pending_reviews_for_batch
    db_session.refresh(unresolved)
    batch_row = unresolved.batch
    create_pending_reviews_for_batch(db_session, batch_row)
    db_session.commit()

    reviews_for_unresolved = db_session.query(SessionNoteReview).filter_by(person_document_id=unresolved.id).all()
    assert reviews_for_unresolved == [], "an unresolved document must never get a SessionNoteReview row at all"
    assert extraction_called["count"] == 0
    assert review_called["count"] == 0

    # And the manual endpoint explicitly refuses too, for defense-in-depth.
    resp = client.post(f"/api/person-documents/{unresolved.id}/review", json={})
    assert resp.status_code == 409
    assert extraction_called["count"] == 0
    assert review_called["count"] == 0
