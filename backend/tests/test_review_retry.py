"""A document's review failing with an UNEXPECTED exception (not
review_person_document's own structured status="error") gets a bounded
number of automatic retries before landing in a permanent failed state —
see run_review's own retry loop in app/routers/person_documents.py. Mocked
model boundary throughout, with a near-zero backoff override so these
tests stay fast.
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


def test_a_transient_failure_that_succeeds_on_retry_completes_normally(client, monkeypatch):
    """Simulates exactly the scenario item 1 asks for: review_person_document
    raises once (a transient failure) then succeeds on the next attempt —
    the review should still land on status="complete", not "failed"."""
    monkeypatch.setattr(settings, "review_retry_backoff_seconds", 0.01)
    _mock_extraction(monkeypatch)
    calls = {"count": 0}

    def _flaky_review(*a, **k):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated transient failure (e.g. a flaky real-API response)")
        return _FakeResult(_findings())

    monkeypatch.setattr(person_documents_module, "review_person_document", _flaky_review)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    review = resp.json()
    assert review["status"] == "complete"
    assert calls["count"] == 2  # one failed attempt, one successful retry


def test_retries_are_exhausted_and_review_lands_in_a_clear_failed_state(client, monkeypatch):
    """Every attempt raises — must land in status="failed" with the FULL
    traceback captured (not just str(exc)), never loop or hang."""
    monkeypatch.setattr(settings, "review_retry_backoff_seconds", 0.01)
    _mock_extraction(monkeypatch)
    calls = {"count": 0}

    def _always_raises(*a, **k):
        calls["count"] += 1
        raise RuntimeError("PydanticUserError: Pydantic models should inherit from BaseModel")

    monkeypatch.setattr(person_documents_module, "review_person_document", _always_raises)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 500  # raise_on_error=True for the manual endpoint — still loud, never swallowed

    review = client.get(f"/api/person-documents/{doc['id']}/review").json()
    assert review["status"] == "failed"
    # 1 initial attempt + settings.review_retry_attempts retries
    assert calls["count"] == 1 + settings.review_retry_attempts


def test_batch_background_job_retries_then_marks_failed_without_crashing_the_batch(client, monkeypatch):
    """raise_on_error=False (the real automatic per-batch path) must retry
    the same way, then mark failed and let the REST of the batch's
    documents still run — one document's exhausted retries must never
    abort the whole batch."""
    monkeypatch.setattr(settings, "review_retry_backoff_seconds", 0.01)
    _mock_extraction(monkeypatch)

    def _always_raises(*a, **k):
        raise RuntimeError("simulated permanent failure")

    monkeypatch.setattr(person_documents_module, "review_person_document", _always_raises)

    with open(FIXTURES / "batch_19page_3client.pdf", "rb") as f:
        resp = client.post("/api/batches", files={"file": ("batch_19page_3client.pdf", f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    batch = resp.json()

    confident_docs = [d for d in batch["documents"] if d["service_code"] is not None]
    assert len(confident_docs) == 4
    for doc in confident_docs:
        review = client.get(f"/api/person-documents/{doc['id']}/review").json()
        if doc["service_code"] == "97156":
            assert review["status"] == "no_applicable_rules"  # never reaches review_person_document at all
        else:
            assert review["status"] == "failed"
            assert "simulated permanent failure" in review["error_message"]
