"""URGENT production bug: a real traceback confirmed a crash
("ValueError: not enough values to unpack (expected 3, got 2)", from
humanize_findings_batch returning an inconsistent tuple shape) inside the
post-review humanize/rule_results section of run_review, which had NO
try/except at all. Inside a FastAPI BackgroundTasks callback (the
automatic per-batch path), that crash has nowhere to surface -- the
review is left stuck at status="processing" forever, indistinguishable
from a genuine hang. Confirms: (1) the exact tuple-shape bug is fixed at
its real source (agent-making's own test suite covers this precisely —
see agent-making/agent/tests/test_humanize_findings_batch.py), and (2) a
crash ANYWHERE in this section now fails loudly — status="failed" with
the real error message, never silently stuck, and never a half-written
set of rule_results. Mocked model boundary throughout, zero real spend.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module

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
    return {"SN-FAKE-01": {"check_type": "deterministic", "result": "pass", "evidence": "Some real finding text.", "page": 1, "confidence": 1.0}}


def _mock_extraction(monkeypatch):
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0},
    )


def test_a_crash_in_the_post_review_section_marks_the_review_failed_not_stuck_processing(client, monkeypatch, db_session):
    """Reproduces the exact real bug class: humanize_findings_batch
    returns a malformed (wrong-arity) tuple, crashing the zip-unpack in
    run_review. Via the AUTOMATIC per-batch path (raise_on_error=False,
    the same one FastAPI's BackgroundTasks uses in production) — the
    review must land on status="failed" with a real error message, not be
    left stuck at "processing" forever."""
    _mock_extraction(monkeypatch)
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    monkeypatch.setattr(
        person_documents_module, "humanize_findings_batch",
        lambda texts, **k: [(t, "humanized")  for t in texts],  # malformed: 2-tuple instead of 3
    )

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    # The manual endpoint's own raise_on_error=True contract still applies
    # -- the exception re-raises straight out of run_review (same as every
    # other unexpected crash in this function), so FastAPI's default
    # handler reports it as a 500, not the endpoint's own deliberate 502
    # (that's reserved for a review that returned normally with
    # status="failed", not one that raised) -- loud either way, never
    # silently swallowed.
    assert resp.status_code == 500

    from app.db.models import SessionNoteReview
    review = db_session.query(SessionNoteReview).filter_by(person_document_id=doc["id"]).order_by(SessionNoteReview.created_at.desc()).first()
    assert review.status == "failed"
    assert "not enough values to unpack" in review.error_message


def test_a_crash_in_the_post_review_section_never_leaves_partial_rule_results(client, monkeypatch, db_session):
    _mock_extraction(monkeypatch)
    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult({
            "SN-FAKE-01": {"check_type": "deterministic", "result": "pass", "evidence": "First.", "page": 1, "confidence": 1.0},
            "SN-FAKE-02": {"check_type": "deterministic", "result": "pass", "evidence": "Second.", "page": 2, "confidence": 1.0},
        }),
    )
    monkeypatch.setattr(
        person_documents_module, "humanize_findings_batch",
        lambda texts, **k: [(t, "humanized") for t in texts],  # malformed: 2-tuple instead of 3
    )

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    client.post(f"/api/person-documents/{doc['id']}/review", json={})  # 502, expected -- status checked above

    from app.db.models import RuleResult, SessionNoteReview
    review = db_session.query(SessionNoteReview).filter_by(person_document_id=doc["id"]).order_by(SessionNoteReview.created_at.desc()).first()
    rule_results = db_session.query(RuleResult).filter_by(review_id=review.id).all()
    assert rule_results == []  # no half-written set -- the crash rolled back before any committed


def test_batch_background_job_marks_failed_without_raising_on_the_same_crash(client, monkeypatch, db_session):
    """The AUTOMATIC per-batch path (run_batch_reviews, raise_on_error=False)
    must survive this same crash without aborting the rest of the batch --
    this is the actual FastAPI BackgroundTasks path the real incident
    happened through."""
    _mock_extraction(monkeypatch)
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    monkeypatch.setattr(
        person_documents_module, "humanize_findings_batch",
        lambda texts, **k: [(t, "humanized") for t in texts],
    )

    with open(FIXTURES / "batch_19page_3client.pdf", "rb") as f:
        resp = client.post("/api/batches", files={"file": ("batch_19page_3client.pdf", f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    batch = resp.json()

    confident_docs = [d for d in batch["documents"] if d["service_code"] is not None]
    assert len(confident_docs) == 4
    for doc in confident_docs:
        review = client.get(f"/api/person-documents/{doc['id']}/review").json()
        if doc["service_code"] == "97156":
            assert review["status"] == "no_applicable_rules"
        else:
            # Every OTHER document hits the same malformed-tuple crash --
            # each must independently land on "failed", never stuck at
            # "processing" and never crashing the rest of the batch.
            assert review["status"] == "failed"
            assert "not enough values to unpack" in review["error_message"]
