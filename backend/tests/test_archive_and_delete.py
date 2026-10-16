"""Archive (soft, reversible) and Delete Forever (a deliberate, narrow
exception to the project's usual no-hard-deletes convention) for cleaning
up known-bad early test audits. All against the mocked model boundary.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module
from app.db.models import AuditLog, PersonDocument, RuleResult, SessionNoteBatch, SessionNoteReview

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str = "batch_19page_3client.pdf") -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_archive_sets_active_false_and_hides_from_default_audits_list(client):
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    resp = client.patch(f"/api/person-documents/{doc['id']}/archive", json={"actor": "k.kumar@masterfaster.org"})
    assert resp.status_code == 200
    assert resp.json()["active"] is False

    default_rows = client.get("/api/audits", params={"page_size": 1000}).json()["items"]
    assert not any(r["person_document_id"] == doc["id"] for r in default_rows)

    with_archived = client.get("/api/audits", params={"include_archived": True, "page_size": 1000}).json()["items"]
    row = next(r for r in with_archived if r["person_document_id"] == doc["id"])
    assert row["active"] is False


def test_archive_is_reversible_via_unarchive(client):
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    client.patch(f"/api/person-documents/{doc['id']}/archive", json={"actor": "k.kumar@masterfaster.org"})
    resp = client.patch(f"/api/person-documents/{doc['id']}/unarchive", json={"actor": "k.kumar@masterfaster.org"})
    assert resp.status_code == 200
    assert resp.json()["active"] is True

    default_rows = client.get("/api/audits", params={"page_size": 1000}).json()["items"]
    assert any(r["person_document_id"] == doc["id"] for r in default_rows)


def test_archive_twice_is_409(client):
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    client.patch(f"/api/person-documents/{doc['id']}/archive", json={"actor": "x"})
    resp = client.patch(f"/api/person-documents/{doc['id']}/archive", json={"actor": "x"})
    assert resp.status_code == 409


def test_archive_writes_an_audit_log_entry(client, db_session):
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    client.patch(f"/api/person-documents/{doc['id']}/archive", json={"actor": "k.kumar@masterfaster.org"})

    entries = db_session.query(AuditLog).filter(
        AuditLog.entity_type == "person_document", AuditLog.entity_id == doc["id"], AuditLog.field == "active",
    ).all()
    assert len(entries) == 1
    assert entries[0].old_value is True
    assert entries[0].new_value is False
    assert entries[0].actor == "k.kumar@masterfaster.org"


def test_delete_forever_removes_document_review_and_rule_results(client, monkeypatch, db_session):
    class _FakeUsage:
        api_calls = 2
        estimated_cost_usd = 0.01

    class _FakeResult:
        status = "complete"
        findings = {
            "SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0},
        }
        usage = _FakeUsage()
        error = None

    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 0, "api_cost_usd": 0.0},
    )

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    review = client.get(f"/api/person-documents/{doc['id']}/review").json()
    assert review["status"] == "complete"  # confirm real rows exist to delete, not an empty no-op

    resp = client.post(
        f"/api/person-documents/{doc['id']}/delete-forever",
        json={"actor": "k.kumar@masterfaster.org", "reason": "known-bad pre-fix test extraction"},
    )
    assert resp.status_code == 204

    db_session.expire_all()
    assert db_session.get(PersonDocument, doc["id"]) is None
    assert db_session.query(SessionNoteReview).filter(SessionNoteReview.person_document_id == doc["id"]).count() == 0
    assert db_session.query(RuleResult).filter(RuleResult.review_id == review["id"]).count() == 0

    # It's really gone — not just archived — so it never shows up even with include_archived=True.
    with_archived = client.get("/api/audits", params={"include_archived": True, "page_size": 1000}).json()["items"]
    assert not any(r["person_document_id"] == doc["id"] for r in with_archived)


def test_delete_forever_writes_an_audit_log_entry_that_survives_the_delete(client, db_session):
    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    resp = client.post(
        f"/api/person-documents/{doc['id']}/delete-forever",
        json={"actor": "k.kumar@masterfaster.org", "reason": "known-bad pre-fix test extraction"},
    )
    assert resp.status_code == 204

    entries = db_session.query(AuditLog).filter(
        AuditLog.entity_type == "person_document", AuditLog.entity_id == doc["id"], AuditLog.field == "deleted_forever",
    ).all()
    assert len(entries) == 1
    assert entries[0].new_value is True
    assert entries[0].actor == "k.kumar@masterfaster.org"


def test_delete_forever_404_for_unknown_id(client):
    resp = client.post(
        "/api/person-documents/does-not-exist/delete-forever",
        json={"actor": "x", "reason": "y"},
    )
    assert resp.status_code == 404


def test_archive_404_for_unknown_id(client):
    resp = client.patch("/api/person-documents/does-not-exist/archive", json={"actor": "x"})
    assert resp.status_code == 404


def _mock_pipeline(monkeypatch):
    class _FakeUsage:
        api_calls = 2
        estimated_cost_usd = 0.01

    class _FakeResult:
        status = "complete"
        findings = {
            "SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0},
        }
        usage = _FakeUsage()
        error = None

    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 0, "api_cost_usd": 0.0},
    )


def test_delete_batch_forever_removes_every_document_review_and_rule_result(client, monkeypatch, db_session):
    """The real test asked for: deletes a batch with N documents, confirms
    all N documents, the batch row, and every rule result/review tied to
    them are gone — and confirms a SEPARATE, untouched batch's own data
    survives, proving this isn't a blanket wipe.
    """
    _mock_pipeline(monkeypatch)
    batch = _upload_batch(client)  # 4 real PersonDocuments: 3 confident + reviewed, 1 no_applicable_rules
    batch_id = batch["id"]
    document_ids = [d["id"] for d in batch["documents"]]
    assert len(document_ids) == 4

    other_batch = _upload_batch(client)  # a SEPARATE batch — must survive untouched
    other_document_ids = [d["id"] for d in other_batch["documents"]]

    review_ids = [
        r.id for r in db_session.query(SessionNoteReview).join(PersonDocument).filter(
            PersonDocument.batch_id == batch_id,
        )
    ]
    assert review_ids  # confirm real rows exist to delete, not an empty no-op

    resp = client.post(
        f"/api/batches/{batch_id}/delete-forever",
        json={"actor": "k.kumar@masterfaster.org", "reason": "misconfigured spend cap, bad partial run"},
    )
    assert resp.status_code == 204, resp.text

    db_session.expire_all()
    assert db_session.get(SessionNoteBatch, batch_id) is None
    for doc_id in document_ids:
        assert db_session.get(PersonDocument, doc_id) is None
    assert db_session.query(SessionNoteReview).filter(SessionNoteReview.id.in_(review_ids)).count() == 0
    assert db_session.query(RuleResult).filter(RuleResult.review_id.in_(review_ids)).count() == 0

    # The other batch's own data must be completely untouched.
    assert db_session.get(SessionNoteBatch, other_batch["id"]) is not None
    for doc_id in other_document_ids:
        assert db_session.get(PersonDocument, doc_id) is not None


def test_delete_batch_forever_writes_an_audit_log_entry_that_survives_the_delete(client, monkeypatch, db_session):
    _mock_pipeline(monkeypatch)
    batch = _upload_batch(client)
    batch_id = batch["id"]

    resp = client.post(
        f"/api/batches/{batch_id}/delete-forever",
        json={"actor": "k.kumar@masterfaster.org", "reason": "misconfigured spend cap, bad partial run"},
    )
    assert resp.status_code == 204

    entries = db_session.query(AuditLog).filter(
        AuditLog.entity_type == "session_note_batch", AuditLog.entity_id == batch_id, AuditLog.field == "deleted_forever",
    ).all()
    assert len(entries) == 1
    assert entries[0].new_value is True
    assert entries[0].actor == "k.kumar@masterfaster.org"


def test_delete_batch_forever_rejects_an_active_run(client, monkeypatch, db_session):
    """A batch whose reviews_active is True (a real run is genuinely in
    progress) must be rejected, not deleted out from under it."""
    _mock_pipeline(monkeypatch)
    batch = _upload_batch(client)
    live_batch = db_session.get(SessionNoteBatch, batch["id"])
    live_batch.reviews_active = True
    db_session.commit()

    resp = client.post(
        f"/api/batches/{batch['id']}/delete-forever",
        json={"actor": "x", "reason": "y"},
    )
    assert resp.status_code == 409

    db_session.expire_all()
    assert db_session.get(SessionNoteBatch, batch["id"]) is not None  # untouched


def test_delete_batch_forever_404_for_unknown_id(client):
    resp = client.post(
        "/api/batches/does-not-exist/delete-forever",
        json={"actor": "x", "reason": "y"},
    )
    assert resp.status_code == 404
