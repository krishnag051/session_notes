"""Three real bugs found via a real, manual CSV audit (Bergstein/Cazi):
1. An ARCHIVED duplicate PersonDocument (the same real visit re-uploaded
   in a separate batch) was still counted as genuine "prior session"
   history, producing a false 100% self-similarity match.
2. review_person_document's own blanket exception handler swallowed a
   real crash into a structured status="error" result instead of a raised
   exception -- the backend's retry mechanism never actually retried it.
3. SN-97151-11 only ever looked at the single document being reviewed,
   never this person's OTHER documents in the same batch/upload.
All exercised here against the mocked model boundary, zero real spend.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module
from app.config import settings

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str = "batch_19page_3client.pdf") -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")}, data={"skip_auto_review": "true"})
    assert resp.status_code == 201, resp.text
    return resp.json()


class _FakeUsage:
    def __init__(self, api_calls=2, cost=0.01):
        self.api_calls = api_calls
        self.estimated_cost_usd = cost


class _FakeResult:
    def __init__(self, findings=None, status="complete", error=None, error_type=None):
        self.status = status
        self.findings = findings or {}
        self.usage = _FakeUsage()
        self.error = error
        self.error_type = error_type


def _mock_extraction(monkeypatch):
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0},
    )


def test_load_prior_extractions_excludes_an_archived_duplicate_document(client, monkeypatch, db_session):
    """Real bug: an archived (active=False) PersonDocument representing
    the SAME real visit re-uploaded in a different batch must never be
    treated as this person's own genuine prior history."""
    from app.db.models import PersonDocument, SessionNoteReview

    _mock_extraction(monkeypatch)
    captured = {}

    def _capture_review(*a, prior_extractions=None, **k):
        captured["prior_extractions"] = prior_extractions
        return _FakeResult()

    monkeypatch.setattr(person_documents_module, "review_person_document", _capture_review)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    # A duplicate of THIS exact document (same person, same service_code),
    # but archived -- simulates the real confirmed scenario (the same
    # source PDF re-uploaded in an earlier batch, later archived).
    duplicate = PersonDocument(
        batch_id=batch["id"], person_id=db_session.get(PersonDocument, doc["id"]).person_id,
        service_code="97153", date_of_service=db_session.get(PersonDocument, doc["id"]).date_of_service,
        appendix="I", page_start=1, page_end=1, classification_confidence="confident", active=False,
    )
    db_session.add(duplicate)
    db_session.flush()
    db_session.add(SessionNoteReview(person_document_id=duplicate.id, status="complete", full_text="duplicate content"))
    db_session.commit()

    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    # This suite's test DB is session-scoped and this same real fixture is
    # uploaded by many other tests too (see test_endpoints.py's own
    # docstring on this), so Bergstein may well have OTHER genuine active
    # prior documents by the time this runs -- an absolute "no prior
    # history at all" assertion would be fragile to test order. The real
    # assertion is narrower and order-independent: the ARCHIVED duplicate's
    # own content must never appear among whatever prior_extractions does
    # surface.
    prior = captured["prior_extractions"] or []
    assert not any(p["full_text"] == "duplicate content" for p in prior)


def test_retry_loop_retries_an_unexpected_structured_error_and_then_succeeds(client, monkeypatch):
    """Real bug: review_person_document's own blanket exception handler
    returns status="error" instead of raising -- the retry loop must treat
    error_type="unexpected" the same as a raised exception (retry it), not
    silently accept it as a permanent, non-retryable failure."""
    monkeypatch.setattr(settings, "review_retry_backoff_seconds", 0.01)
    _mock_extraction(monkeypatch)
    calls = {"count": 0}

    def _fails_once_then_succeeds(*a, **k):
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeResult(status="error", error="PydanticUserError: ...", error_type="unexpected")
        return _FakeResult(findings={"SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0}})

    monkeypatch.setattr(person_documents_module, "review_person_document", _fails_once_then_succeeds)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "complete"
    assert calls["count"] == 2


def test_retry_loop_does_not_retry_a_cap_exceeded_structured_error(client, monkeypatch):
    """A deliberate limit (spend/call cap) retrying would just waste
    another real call for the identical outcome -- must fail immediately,
    not retry."""
    _mock_extraction(monkeypatch)
    calls = {"count": 0}

    def _always_cap_exceeded(*a, **k):
        calls["count"] += 1
        return _FakeResult(status="error", error="ApiSpendCapExceeded: ...", error_type="cap_exceeded")

    monkeypatch.setattr(person_documents_module, "review_person_document", _always_cap_exceeded)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 502  # this endpoint's existing, pre-this-phase contract for a failed review
    review = client.get(f"/api/person-documents/{doc['id']}/review").json()
    assert review["status"] == "failed"
    assert calls["count"] == 1  # no retry at all


def test_sibling_documents_are_threaded_into_review_person_document(client, monkeypatch):
    """Real bug: SN-97151-11 needs this person's OTHER documents from the
    same batch/upload -- confirms the backend actually fetches and passes
    them, using the real Cazi 97151+97156 fixture pair."""
    _mock_extraction(monkeypatch)
    captured = {}

    def _capture_review(*a, sibling_documents=None, **k):
        captured["sibling_documents"] = sibling_documents
        return _FakeResult()

    monkeypatch.setattr(person_documents_module, "review_person_document", _capture_review)

    batch = _upload_batch(client)
    doc_151 = next(d for d in batch["documents"] if d["service_code"] == "97151")
    resp = client.post(f"/api/person-documents/{doc_151['id']}/review", json={})
    assert resp.status_code == 201, resp.text

    siblings = captured["sibling_documents"]
    assert siblings, "expected at least one sibling document"
    sibling_codes = {s["service_code"] for s in siblings}
    assert "97156" in sibling_codes
    sib_156 = next(s for s in siblings if s["service_code"] == "97156")
    assert "Parent Goals Addressed" in sib_156["full_text"]


def test_sibling_documents_never_leak_across_different_patients_in_the_same_batch(client, monkeypatch):
    """CRITICAL real bug found via a real manual audit (Aiza Nabiha): a
    batch upload is NOT one patient per batch -- a single real upload can
    and does contain many different patients' documents together (this
    fixture itself: Bergstein, Cazi, and Drummer, all one batch_id). The
    sibling-document query originally filtered ONLY by batch_id, with no
    person_id filter at all -- so a patient with NO real sibling of their
    own (Bergstein has no 97156 document anywhere) was being handed
    ANOTHER REAL PATIENT'S document (Cazi's own real 97156 'Parent Goals
    Addressed' document, sitting in the same batch_id) as if it were his
    own sibling. Confirms the fix: Bergstein's own sibling lookup must
    never surface Cazi's content, even though both are in the same batch.
    """
    _mock_extraction(monkeypatch)
    captured = {}

    def _capture_review(*a, sibling_documents=None, **k):
        captured["sibling_documents"] = sibling_documents
        return _FakeResult()

    monkeypatch.setattr(person_documents_module, "review_person_document", _capture_review)

    batch = _upload_batch(client)
    bergstein_doc = next(d for d in batch["documents"] if d["service_code"] == "97153" and d["page_start"] < 8)
    resp = client.post(f"/api/person-documents/{bergstein_doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text

    siblings = captured["sibling_documents"]
    assert not siblings, f"Bergstein has no real sibling of his own, but got: {siblings}"
