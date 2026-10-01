"""Batch-upload/review/override/merge endpoint tests — all against the
mocked model boundary, synthetic prior_extractions, zero real spend.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str, uploaded_by: str | None = None) -> dict:
    data = {"uploaded_by": uploaded_by} if uploaded_by else {}
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")}, data=data)
    assert resp.status_code == 201, resp.text
    return resp.json()


class _FakeUsage:
    def __init__(self, api_calls=2, cost=0.01):
        self.api_calls = api_calls
        self.estimated_cost_usd = cost


class _FakeResult:
    def __init__(self, findings, api_calls=2, cost=0.01, status="complete", error=None, error_type=None):
        self.status = status
        self.findings = findings
        self.usage = _FakeUsage(api_calls, cost)
        self.error = error
        # "cap_exceeded" (non-retryable) vs "unexpected" (the backend's own
        # retry loop retries this one) — see app/routers/person_documents.py
        # ::run_review and agent-making's ReviewResult.error_type docstring.
        self.error_type = error_type or ("cap_exceeded" if status == "error" else None)


def _fake_findings(overrides: dict | None = None) -> dict:
    base = {
        "SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "Signed within 2 days.", "page": 6, "confidence": 1.0},
        "SN-97153-06": {"check_type": "judgment", "result": "fail", "evidence": "Off-topic content found.", "page": 4, "confidence": 0.9},
        "SN-97153-04": {"check_type": "not_checkable", "result": "not_checkable", "evidence": "Needs a timesheet.", "page": None, "confidence": 0.0},
        "SN-97153-21": {"check_type": "deterministic", "result": "pass", "evidence": "Not supervised.", "page": 2, "confidence": 1.0},
    }
    if overrides:
        base.update(overrides)
    return base


def test_upload_batch_creates_people_and_documents(client):
    batch = _upload_batch(client, "batch_19page_3client.pdf", uploaded_by="k.kumar@masterfaster.org")
    assert batch["uploaded_by"] == "k.kumar@masterfaster.org"
    assert len(batch["admin_noise_pages"]) == 1
    names = {d["service_code"] for d in batch["documents"] if d["service_code"]}
    assert "97153" in names
    # Cazi's two appendices (97151 + 97156) should both be present as their own documents.
    person_ids = {d["person_id"] for d in batch["documents"] if d["person_id"]}
    assert len(person_ids) == 3  # Bergstein, Cazi, Drummer


def test_get_batch_round_trips(client):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    resp = client.get(f"/api/batches/{batch['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == batch["id"]


def test_get_batch_404_for_unknown_id(client):
    resp = client.get("/api/batches/does-not-exist")
    assert resp.status_code == 404


def test_uploading_the_same_batch_twice_reuses_the_same_person(client):
    batch1 = _upload_batch(client, "batch_19page_3client.pdf")
    batch2 = _upload_batch(client, "batch_19page_3client.pdf")
    ids1 = {d["person_id"] for d in batch1["documents"] if d["person_id"]}
    ids2 = {d["person_id"] for d in batch2["documents"] if d["person_id"]}
    assert ids1 == ids2  # same three real people, matched by global_key, not duplicated


def test_review_document_mocked_writes_review_and_rule_results(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult(_fake_findings()),
    )

    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={"actor": "k.kumar@masterfaster.org"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["score"] == 50.0  # 1 pass (16) + 1 fail (06) among scored findings -- 04 not_checkable and 21 Informational excluded
    assert body["audit_result"] == "fail"  # 50% is below the 80% default threshold
    assert body["reviewed"] is False  # review status is a SEPARATE axis, untouched by this endpoint
    assert body["api_calls_used"] == 2
    assert len(body["grouped_results"]["Failed"]) == 1
    assert len(body["grouped_results"]["Not Applicable"]) == 1
    assert len(body["grouped_results"]["Informational"]) == 1
    assert len(body["grouped_results"]["Passed"]) == 1


def test_review_document_all_pass_rolls_up_to_pass(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult(_fake_findings({"SN-97153-06": {"check_type": "judgment", "result": "pass", "evidence": "on topic", "page": 4, "confidence": 0.9}})),
    )
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["score"] == 100.0
    assert body["audit_result"] == "pass"


def test_review_document_uncertain_counts_as_failed_for_scoring(client, monkeypatch):
    # "uncertain" has no separate score bucket of its own -- it counts as
    # failed for compute_score (same bucket the Failed/Not Applicable/
    # Informational/Passed display grouping already puts it in). One pass
    # (SN-97153-16) + one uncertain-as-failed (SN-97153-06) = 50% score,
    # which is below the default 80% threshold -> "fail".
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult(_fake_findings({"SN-97153-06": {"check_type": "judgment", "result": "uncertain", "evidence": "split vote", "page": None, "confidence": 0.0}})),
    )
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["score"] == 50.0
    assert body["audit_result"] == "fail"


def test_reviewing_a_document_never_counts_it_as_its_own_history(client, monkeypatch):
    """A person_document's OWN prior reviews must never feed back into its
    own prior_extractions (PersonDocument.id != person_document.id in the
    query) -- reviewing the SAME document twice must yield the exact same
    prior_extractions both times, since neither call adds anything NEW to
    "this person's OTHER documents". Deliberately order-independent (never
    asserts an absolute None/empty state) -- this suite's test database is
    session-scoped and real fixture files map to the same real Person via
    global_key across many tests, so an absolute "no prior review exists
    yet" assumption would be fragile to test execution order.
    """
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    captured = []

    def _fake_review(pdf_path, service_code, *, prior_extractions=None, model_override=None, timesheet_rows=None, appendix=None, sibling_documents=None, max_spend_usd=None):
        captured.append(prior_extractions)
        return _FakeResult(_fake_findings())

    monkeypatch.setattr(person_documents_module, "review_person_document", _fake_review)

    assert client.post(f"/api/person-documents/{doc['id']}/review", json={}).status_code == 201
    assert client.post(f"/api/person-documents/{doc['id']}/review", json={}).status_code == 201

    assert captured[0] == captured[1]


def test_review_unresolved_document_returns_409(client, db_session):
    # None of this suite's real fixtures happen to produce a genuine
    # unresolved page range (confirmed: 0 unresolved across all of them,
    # per agent-making's own Phase 1 ground truth) -- insert one directly
    # to exercise this guard rather than skip testing it.
    from app.db.models import PersonDocument

    batch = _upload_batch(client, "batch_19page_3client.pdf")
    unresolved = PersonDocument(
        batch_id=batch["id"], person_id=None, service_code=None, date_of_service=None,
        appendix=None, page_start=1, page_end=1, classification_confidence="unresolved",
        classification_note="synthetic, for this test only",
    )
    db_session.add(unresolved)
    db_session.commit()

    resp = client.post(f"/api/person-documents/{unresolved.id}/review", json={})
    assert resp.status_code == 409


def test_review_document_404_for_unknown_id(client):
    resp = client.post("/api/person-documents/does-not-exist/review", json={})
    assert resp.status_code == 404


def test_review_document_502_when_pipeline_returns_error(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult({}, status="error", error="simulated pipeline failure"),
    )
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 502


def test_get_person_document_works_before_any_review_exists(client):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.get(f"/api/person-documents/{doc['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == doc["id"]
    assert body["client_name"] is not None
    assert body["service_code"] == "97153"


def test_get_person_document_404_for_unknown_id(client):
    resp = client.get("/api/person-documents/does-not-exist")
    assert resp.status_code == 404


def test_get_latest_review_returns_most_recent(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_fake_findings()))
    client.post(f"/api/person-documents/{doc['id']}/review", json={})
    resp = client.get(f"/api/person-documents/{doc['id']}/review")
    assert resp.status_code == 200
    assert resp.json()["person_document_id"] == doc["id"]


def test_get_latest_review_pending_right_after_upload(client):
    """Batch upload now creates a pending review row (and queues its real
    run) for every confidently-classified document immediately — see
    POST /batches — so "no review yet" is no longer true the instant
    after upload the way it was before this phase; this replaces the old
    "404 when none exists" case (see the unresolved-document test below
    for the genuine 404 case that still exists)."""
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.get(f"/api/person-documents/{doc['id']}/review")
    assert resp.status_code == 200
    # No mocking active in this test, so the auto-triggered background run
    # already hit the (blocked-by-default) real call site and failed —
    # itself proof the auto-review wiring actually ran, not a "we forgot
    # to check" gap.
    assert resp.json()["status"] == "failed"


def test_get_latest_review_404_for_unresolved_document_with_no_review_row(client, db_session):
    from app.db.models import PersonDocument

    batch = _upload_batch(client, "batch_19page_3client.pdf")
    unresolved = PersonDocument(
        batch_id=batch["id"], person_id=None, service_code=None, date_of_service=None,
        appendix=None, page_start=1, page_end=1, classification_confidence="unresolved",
        classification_note="synthetic, for this test only",
    )
    db_session.add(unresolved)
    db_session.commit()

    resp = client.get(f"/api/person-documents/{unresolved.id}/review")
    assert resp.status_code == 404


def test_list_person_documents(client):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["person_id"])
    resp = client.get(f"/api/people/{doc['person_id']}/documents")
    assert resp.status_code == 200
    assert any(d["id"] == doc["id"] for d in resp.json())


def test_mark_reviewed_sets_reviewed_axis_only(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult(_fake_findings({"SN-97153-06": {"check_type": "judgment", "result": "fail", "evidence": "x", "page": 1, "confidence": 0.9}})),
    )
    review = client.post(f"/api/person-documents/{doc['id']}/review", json={}).json()
    assert review["audit_result"] == "fail"

    resp = client.patch(f"/api/session-note-reviews/{review['id']}/mark-reviewed", json={"reviewed_by": "k.kumar@masterfaster.org"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviewed"] is True
    assert body["reviewed_by"] == "k.kumar@masterfaster.org"
    # Audit Flags is untouched by Review Status changing -- still "fail".
    assert body["audit_result"] == "fail"


def test_mark_reviewed_404_for_unknown_id(client):
    resp = client.patch("/api/session-note-reviews/does-not-exist/mark-reviewed", json={"reviewed_by": "x"})
    assert resp.status_code == 404


def test_override_rule_result_sets_final_never_model(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_fake_findings()))
    review = client.post(f"/api/person-documents/{doc['id']}/review", json={}).json()

    failed_rule = review["grouped_results"]["Failed"][0]
    original_model_status = failed_rule["model_status"]

    resp = client.patch(
        f"/api/rule-results/{failed_rule['id']}",
        json={"final_status": "pass", "final_finding": "Reviewer confirmed this is fine.", "override_reason": "checked manually", "actor": "k.kumar@masterfaster.org"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["final_status"] == "pass"
    assert body["overridden_by"] == "k.kumar@masterfaster.org"
    assert body["override_reason"] == "checked manually"

    # model_* must be completely unchanged -- re-fetch the review and confirm.
    review_after = client.get(f"/api/person-documents/{doc['id']}/review").json()
    same_rule = next(r for r in review_after["grouped_results"]["Passed"] if r["id"] == failed_rule["id"])
    assert same_rule["model_status"] == original_model_status  # never touched
    assert same_rule["is_overridden"] is True
    # Overriding the one Failed rule to pass should flip the whole review's audit_result too.
    assert review_after["audit_result"] == "pass"


def test_override_rule_result_404_for_unknown_id(client):
    resp = client.patch("/api/rule-results/does-not-exist", json={"final_status": "pass", "override_reason": "x", "actor": "y"})
    assert resp.status_code == 404


def test_override_rule_result_requires_at_least_one_field(client, monkeypatch):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_fake_findings()))
    review = client.post(f"/api/person-documents/{doc['id']}/review", json={}).json()
    any_rule_id = review["grouped_results"]["Failed"][0]["id"] if review["grouped_results"]["Failed"] else review["grouped_results"]["Passed"][0]["id"]

    resp = client.patch(f"/api/rule-results/{any_rule_id}", json={"override_reason": "x", "actor": "y"})
    assert resp.status_code == 400


def test_merge_person_stub_returns_501(client):
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    person_id = next(d["person_id"] for d in batch["documents"] if d["person_id"])
    resp = client.post(f"/api/people/{person_id}/merge", json={"merge_into_person_id": "other-id", "reason": "same person, different name spelling", "actor": "k.kumar@masterfaster.org"})
    assert resp.status_code == 501


def test_merge_person_404_for_unknown_id(client):
    resp = client.post("/api/people/does-not-exist/merge", json={"merge_into_person_id": "x", "reason": "y", "actor": "z"})
    assert resp.status_code == 404
