"""Urgent production ask: a hard, ENFORCED $2/document spend ceiling
(settings.per_document_hard_cap_usd) spanning ALL THREE real call stages
(extraction, review, humanize) combined — not just a monitoring/alert.
Confirms run_review actually computes and forwards each stage's own
REMAINING headroom as its max_spend_usd, so the three stages combined can
never exceed the cap. Mocked model boundary throughout, zero real spend.
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
    def __init__(self, findings=None, cost=0.01):
        self.status = "complete"
        self.findings = findings or {}
        self.usage = _FakeUsage(cost=cost)
        self.error = None


def test_extraction_gets_the_full_cap_as_remaining_budget_on_the_first_attempt(client, monkeypatch):
    captured = {}

    def _fake_extraction(*a, max_spend_usd=None, **k):
        captured["extraction_max_spend_usd"] = max_spend_usd
        return {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0}

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _fake_extraction)
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult())

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    assert captured["extraction_max_spend_usd"] == settings.per_document_hard_cap_usd


def test_review_person_document_gets_reduced_budget_after_extraction_spends_some(client, monkeypatch):
    captured = {}

    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.75},
    )

    def _fake_review(*a, max_spend_usd=None, **k):
        captured["review_max_spend_usd"] = max_spend_usd
        return _FakeResult()

    monkeypatch.setattr(person_documents_module, "review_person_document", _fake_review)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    # $2.00 cap - $0.75 already spent on extraction = $1.25 left for review.
    assert captured["review_max_spend_usd"] == round(settings.per_document_hard_cap_usd - 0.75, 10)


def test_humanize_pass_gets_zero_budget_once_extraction_and_review_already_hit_the_cap(client, monkeypatch):
    captured = {}

    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 1.20},
    )
    monkeypatch.setattr(
        person_documents_module, "review_person_document",
        lambda *a, **k: _FakeResult({"SN-FAKE-01": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 1, "confidence": 1.0}}, cost=0.90),
    )

    def _fake_humanize(texts, *, labels=None, max_spend_usd=None):
        captured["humanize_max_spend_usd"] = max_spend_usd
        return [(t, t, {}) for t in texts]

    monkeypatch.setattr(person_documents_module, "humanize_findings_batch", _fake_humanize)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    # $1.20 + $0.90 = $2.10, already over the $2.00 cap -- humanize gets
    # exactly $0 remaining, never a negative number.
    assert captured["humanize_max_spend_usd"] == 0.0
