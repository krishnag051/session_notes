from pathlib import Path

import app.routers.person_documents as person_documents_module
from app.db.models import Reviewer

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str) -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()


class _FakeUsage:
    api_calls = 2
    estimated_cost_usd = 0.01


class _FakeResult:
    def __init__(self, findings):
        self.status = "complete"
        self.findings = findings
        self.usage = _FakeUsage()
        self.error = None


def _findings(overrides=None):
    base = {
        "SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0},
        "SN-97153-06": {"check_type": "judgment", "result": "pass", "evidence": "ok", "page": 4, "confidence": 0.9},
    }
    if overrides:
        base.update(overrides)
    return base


def test_audits_list_includes_a_row_for_an_unreviewed_document(client):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    resp = client.get("/api/audits", params={"page_size": 1000})
    assert resp.status_code == 200
    row = next(r for r in resp.json()["items"] if r["person_document_id"] == doc["id"])
    assert row["review_status"] == "Not Reviewed"
    assert row["audit_flags"] is None
    assert row["score"] is None
    assert row["client"] is not None
    assert row["code"] == "97153"


def test_audits_list_reflects_a_completed_review(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    client.post(f"/api/person-documents/{doc['id']}/review", json={})

    resp = client.get("/api/audits", params={"page_size": 1000})
    row = next(r for r in resp.json()["items"] if r["person_document_id"] == doc["id"])
    assert row["score"] == 100.0
    assert row["audit_flags"] == "PASSED"
    assert row["review_status"] == "Not Reviewed"  # audit_result and reviewed are still independent axes
    assert row["provider"] is not None  # real, zero-cost structural extraction from the real fixture
    assert row["start_time"] is not None


def test_audits_list_excludes_unresolved_documents(client):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    unresolved_ids = {d["id"] for d in batch["documents"] if d["classification_confidence"] == "unresolved"}
    resp = client.get("/api/audits", params={"page_size": 1000})
    listed_ids = {r["person_document_id"] for r in resp.json()["items"]}
    assert not (unresolved_ids & listed_ids)


def test_mark_reviewed_auto_creates_a_new_reviewer(client, monkeypatch, db_session):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    review = client.post(f"/api/person-documents/{doc['id']}/review", json={}).json()

    unique_name = "Test Reviewer Auto Create"
    resp = client.patch(f"/api/session-note-reviews/{review['id']}/mark-reviewed", json={"reviewed_by": unique_name})
    assert resp.status_code == 200
    assert resp.json()["reviewed_by"] == unique_name

    reviewer_resp = client.get("/api/reviewers", params={"q": "Test Reviewer"})
    assert any(r["name"] == unique_name for r in reviewer_resp.json())


def test_mark_reviewed_reuses_existing_reviewer_case_insensitively(client, monkeypatch, db_session):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    review = client.post(f"/api/person-documents/{doc['id']}/review", json={}).json()

    unique_name = "Case Sensitivity Test Reviewer"
    client.patch(f"/api/session-note-reviews/{review['id']}/mark-reviewed", json={"reviewed_by": unique_name})
    client.patch(f"/api/session-note-reviews/{review['id']}/mark-reviewed", json={"reviewed_by": unique_name.upper()})

    count = db_session.query(Reviewer).filter(Reviewer.name.ilike(unique_name)).count()
    assert count == 1  # never duplicated despite the case difference


def test_mark_unreviewed_clears_the_reviewed_axis_only(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    review = client.post(f"/api/person-documents/{doc['id']}/review", json={}).json()
    client.patch(f"/api/session-note-reviews/{review['id']}/mark-reviewed", json={"reviewed_by": "Someone"})

    resp = client.patch(f"/api/session-note-reviews/{review['id']}/mark-unreviewed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviewed"] is False
    assert body["reviewed_by"] is None
    assert body["reviewed_at"] is None
    assert body["audit_result"] == "pass"  # untouched by unmarking


def test_create_reviewer_via_settings_form(client):
    resp = client.post("/api/reviewers", json={"name": "Settings Added Reviewer", "email": "sar@example.com"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Settings Added Reviewer"


def test_create_reviewer_reuses_existing_by_name(client):
    client.post("/api/reviewers", json={"name": "Dup Check Reviewer"})
    resp = client.post("/api/reviewers", json={"name": "dup check reviewer"})
    assert resp.status_code == 201
    listing = client.get("/api/reviewers", params={"q": "Dup Check"}).json()
    assert sum(1 for r in listing if r["name"].lower() == "dup check reviewer") == 1


def test_mark_unreviewed_404_for_unknown_id(client):
    resp = client.patch("/api/session-note-reviews/does-not-exist/mark-unreviewed")
    assert resp.status_code == 404


def test_list_rules_returns_the_real_rules_json(client):
    resp = client.get("/api/rules")
    assert resp.status_code == 200
    rules = resp.json()
    assert len(rules) == 65
    ids = {r["rule_id"] for r in rules}
    assert "SN-97151-01" in ids
    first = next(r for r in rules if r["rule_id"] == "SN-97151-01")
    assert first["codes"] == ["97151"]
    assert first["check_type"] == "judgment"


def test_pass_rate_is_null_not_zero_with_no_reviews():
    """"Nothing scored yet" and "everything failed" must never render as
    the same number. Unit-tests the pure arithmetic directly rather than
    the full endpoint against a real empty DB, since this suite's shared
    sqlite file (no per-test reset) can't guarantee a true zero-review
    state depending on test ordering."""
    from app.routers.audits import _pass_rate

    assert _pass_rate(total_audits_completed=0, pass_count=0) is None
    assert _pass_rate(total_audits_completed=4, pass_count=3) == 75.0


def test_audit_summary_reflects_uploads_and_reviews(client, monkeypatch):
    # This DB is shared across the whole test session (no per-test reset),
    # so we assert deltas caused by THIS test's own actions, never absolute
    # totals — other tests' rows are already sitting in the same table.
    before = client.get("/api/audits/summary").json()

    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    mid = client.get("/api/audits/summary").json()
    # Person.global_key persists across uploads (same fixture re-uploaded by
    # an earlier test resolves to the SAME 3 people), so this can only ever
    # go up, never down or by a fixed +3 — that's the identity model working
    # as intended, not a bug in this assertion.
    assert mid["total_patients_covered"] >= before["total_patients_covered"]
    assert mid["total_audits_completed"] == before["total_audits_completed"]

    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    client.post(f"/api/person-documents/{doc['id']}/review", json={})

    after = client.get("/api/audits/summary").json()
    assert after["total_audits_completed"] == before["total_audits_completed"] + 1
    assert after["audits_this_week"] >= before["audits_this_week"] + 1
    assert isinstance(after["pass_rate"], float)
