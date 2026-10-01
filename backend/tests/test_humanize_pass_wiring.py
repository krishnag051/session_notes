"""Priority 2 of a real manual audit round: the post-review humanization
pass (a real, separately-billed Haiku call per finding, run AFTER rule-
checking completes) replaces the old client-side string-truncation
heuristic. Confirms the backend actually calls it, stores BOTH the raw and
humanized text as separate permanent columns, and folds its real cost into
the review's own spend/call tracking. Mocked model boundary throughout.
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
    return {
        "SN-97153-16": {
            "check_type": "deterministic", "result": "pass",
            "evidence": "Attendance section has 'Peer' checked as present [Page 1], but the narrative never mentions a peer.",
            "page": 1, "confidence": 1.0,
        },
    }


def _mock_extraction(monkeypatch):
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0},
    )


def test_humanize_pass_is_called_with_the_raw_finding_text_and_stores_both_versions(client, monkeypatch):
    _mock_extraction(monkeypatch)
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    captured = {}

    def _fake_humanize(texts, *, labels=None, **k):
        captured["texts"] = texts
        captured["labels"] = labels
        return [(t, f"Short: {t[:20]}...", {"input_tokens": 50, "output_tokens": 20, "cost_usd": 0.002}) for t in texts]

    monkeypatch.setattr(person_documents_module, "humanize_findings_batch", _fake_humanize)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text

    assert captured["labels"] == ["SN-97153-16"]
    assert "but the narrative never mentions a peer" in captured["texts"][0]

    review = resp.json()
    passed = review["grouped_results"]["Passed"]
    assert len(passed) == 1
    assert passed[0]["final_finding"].startswith("Short: ")
    assert passed[0]["model_finding_raw"] == _findings()["SN-97153-16"]["evidence"]


def test_humanize_pass_cost_is_folded_into_review_spend(client, monkeypatch):
    _mock_extraction(monkeypatch)
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(_findings()))
    monkeypatch.setattr(
        person_documents_module, "humanize_findings_batch",
        lambda texts, **k: [(t, t, {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.005}) for t in texts],
    )

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    review = resp.json()
    # 0.01 (review_person_document) + 0.005 (humanize) -- extraction mocked at $0
    assert review["spend_usd"] == 0.015
    assert review["api_calls_used"] == 4  # 1 (mocked extraction) + 2 (review) + 1 (humanize, one real call made)


def test_humanize_pass_failure_for_one_finding_falls_back_to_raw_text_for_that_finding_only(client, monkeypatch):
    """Real bug the reference project's own humanize_findings was built to
    fix, verified end to end through this backend's own wiring too."""
    _mock_extraction(monkeypatch)
    findings = {
        "SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "Finding A raw text.", "page": 1, "confidence": 1.0},
        "SN-FAKE-99": {"check_type": "deterministic", "result": "pass", "evidence": "Finding B raw text.", "page": 2, "confidence": 1.0},
    }
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult(findings))

    def _fake_humanize(texts, *, labels=None, **k):
        results = []
        for t in texts:
            if "Finding B" in t:
                results.append((t, t, {}))  # simulated failure -- raw text unchanged
            else:
                results.append((t, "Humanized A.", {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001}))
        return results

    monkeypatch.setattr(person_documents_module, "humanize_findings_batch", _fake_humanize)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
    passed = {r["rule_id"]: r for r in resp.json()["grouped_results"]["Passed"]}
    assert passed["SN-97153-16"]["final_finding"] == "Humanized A."
    assert passed["SN-FAKE-99"]["final_finding"] == "Finding B raw text."
