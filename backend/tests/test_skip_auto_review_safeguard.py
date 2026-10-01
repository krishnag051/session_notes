"""The actual fix for the real, unplanned-spend incident during manual
verification (see this project's own incident memory): a batch upload
against ANY live-keyed backend auto-triggers a real, billed review per
document, with no confirmation step in between. skip_auto_review=true
lets a manual/verification upload classify a batch (documents, appendices,
pages all still fully inspectable) WITHOUT ever scheduling that real
review. Mocked model boundary throughout — if this regresses to scheduling
the real review anyway, the autouse guardrail (or the raising fakes below)
catches it.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _must_not_be_called(*a, **k):
    raise AssertionError("real pipeline call site was invoked despite skip_auto_review=true")


def test_skip_auto_review_classifies_but_never_calls_the_real_pipeline(client, monkeypatch):
    monkeypatch.setattr(person_documents_module, "review_person_document", _must_not_be_called)
    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _must_not_be_called)

    with open(FIXTURES / "batch_19page_3client.pdf", "rb") as f:
        resp = client.post(
            "/api/batches",
            files={"file": ("batch_19page_3client.pdf", f, "application/pdf")},
            data={"skip_auto_review": "true"},
        )
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    assert batch["auto_review_skipped"] is True

    # Classification itself still ran in full — nothing about inspecting
    # the batch is degraded, only the real-API review is withheld.
    confident_docs = [d for d in batch["documents"] if d["service_code"] is not None]
    assert len(confident_docs) == 4

    # Every confident document's own pending review row still exists
    # (created up front, same as always) but never advances past pending —
    # the background task that would run it was never scheduled.
    for doc in confident_docs:
        review = client.get(f"/api/person-documents/{doc['id']}/review").json()
        assert review["status"] == "pending"


def test_omitting_skip_auto_review_still_reviews_automatically_as_before(client, monkeypatch):
    """Regression guard on the safeguard itself: normal production usage
    (no skip_auto_review at all) must keep working exactly as before —
    this flag is opt-in, never a silent behavior change for real uploads."""
    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: type("_R", (), {
            "status": "complete",
            "findings": {"SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0}},
            "usage": type("_U", (), {"api_calls": 2, "estimated_cost_usd": 0.01})(),
            "error": None,
        })(),
    )
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0},
    )

    with open(FIXTURES / "batch_19page_3client.pdf", "rb") as f:
        resp = client.post(
            "/api/batches",
            files={"file": ("batch_19page_3client.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    assert batch["auto_review_skipped"] is False

    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    review = client.get(f"/api/person-documents/{doc['id']}/review").json()
    assert review["status"] == "complete"
