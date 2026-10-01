"""97156 (family/caregiver guidance) has NO rules.json entries at all —
rules.json's own top-level description documents this explicitly. Running
the real pipeline against it used to burn real spend for zero checking
value and land on a misleading 100% ("nothing to fail") score. Verifies
the fix skips both real calls entirely and reports an honest, distinct
terminal status instead. Mocked model boundary throughout — if this
regresses to actually calling the real pipeline for 97156, the
autouse guardrail catches it (raises), which these tests would surface as
a failure, not a silent real spend.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str = "batch_19page_3client.pdf") -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_has_applicable_rules():
    from app.services.review import has_applicable_rules

    assert has_applicable_rules("97151") is True
    assert has_applicable_rules("97153") is True
    assert has_applicable_rules("97156") is False  # the real, documented gap
    assert has_applicable_rules("not-a-real-code") is False


def test_97156_document_auto_review_skips_real_calls_and_reports_honest_status(client, monkeypatch):
    """Batch upload auto-triggers review for every confident document,
    97156 included — this must reach status="no_applicable_rules"
    WITHOUT ever calling either real call site. Monkeypatching them to
    raise (rather than the usual fake-success pattern) is itself the
    proof they were never invoked."""
    def _must_not_be_called(*a, **k):
        raise AssertionError("real pipeline call site was invoked for a 97156 document — should have been skipped")

    monkeypatch.setattr(person_documents_module, "review_person_document", _must_not_be_called)
    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _must_not_be_called)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97156")

    review = client.get(f"/api/person-documents/{doc['id']}/review").json()
    assert review["status"] == "no_applicable_rules"
    assert review["score"] is None
    assert review["audit_result"] is None
    assert review["grouped_results"] == {"Failed": [], "Not Applicable": [], "Informational": [], "Passed": []}
    # Free, zero-cost fields still populated — the document itself is fine.
    assert review["provider_name"] is not None


def test_manual_rerun_of_97156_also_reports_no_applicable_rules(client, monkeypatch):
    """The exact real-world repair path: a document whose review predates
    this fix gets manually re-run through the normal endpoint and lands on
    the corrected, honest status — not a raw data edit."""
    def _must_not_be_called(*a, **k):
        raise AssertionError("real pipeline call site was invoked for a 97156 document — should have been skipped")

    monkeypatch.setattr(person_documents_module, "review_person_document", _must_not_be_called)
    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _must_not_be_called)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97156")

    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={"actor": "k.kumar@masterfaster.org"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "no_applicable_rules"
    assert body["score"] is None


def test_audits_list_shows_null_flags_not_misleading_pass_for_no_applicable_rules(client, monkeypatch):
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(person_documents_module, "extract_session_note_data", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97156")

    rows = client.get("/api/audits", params={"page_size": 1000}).json()["items"]
    row = next(r for r in rows if r["person_document_id"] == doc["id"])
    assert row["processing_status"] == "no_applicable_rules"
    assert row["audit_flags"] is None  # never a misleading PASSED
    assert row["score"] is None
