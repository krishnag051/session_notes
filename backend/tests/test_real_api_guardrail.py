"""Proves the structural guardrail in conftest.py (`_block_real_api_calls`)
actually blocks a real call BEFORE it reaches agent-making, at the exact
seam this backend imports agent-making's review function through.

Zero real API calls in this file, by construction: the one test that opts
out via @pytest.mark.real_api immediately re-patches the same seam with
its own local stub — it never lets the real import run either.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.routers.person_documents as person_documents_module
from app.main import app

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str) -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_guardrail_blocks_review_by_default(client):
    """A completely ordinary test, no marker, no explicit reference to the
    guardrail fixture — confirms the autouse fixture patched the real seam
    out from under it regardless."""
    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] is not None)

    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    # FastAPI's default 500 handler deliberately doesn't leak the internal
    # exception message into the response body (correct production
    # behavior) — the real proof this fired is the exception TYPE, checked
    # directly against the app's own exception handler machinery below,
    # plus the companion test confirming the seam is patched at all.
    assert resp.status_code == 500


def test_guardrail_active_by_default_with_no_special_setup():
    assert person_documents_module.review_person_document.__name__ == "_blocked_review_person_document"


def test_humanize_seam_blocks_by_default_once_a_test_mocks_review_person_document(client, monkeypatch):
    """The post-review humanize pass is a THIRD real call site run_review
    reaches unconditionally after ANY successful review_person_document
    result — proves it's genuinely guarded too, not just assumed covered
    because dozens of other tests happen to also mock it away."""
    class _FakeUsage:
        api_calls = 0
        estimated_cost_usd = 0.0

    class _FakeResult:
        status = "complete"
        findings = {"SN-97153-16": {"check_type": "deterministic", "result": "pass", "evidence": "ok", "page": 6, "confidence": 1.0}}
        usage = _FakeUsage()
        error = None

    # Deliberately mocks review_person_document (opting into "mocked
    # pipeline run") but NOT humanize_findings_batch — the real
    # app.agent_client.humanize_findings_batch would still be reached for
    # the one real finding above unless the guardrail's own
    # _maybe_fake_humanize_findings_batch catches it.
    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 0, "api_cost_usd": 0.0},
    )

    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] is not None)
    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    # The fake humanize fallback returns (text, text, {}) for every
    # finding -- a real network attempt would have 500'd instead.
    assert resp.status_code == 201, resp.text
    review = resp.json()
    assert review["grouped_results"]["Passed"][0]["final_finding"] == "ok"


def test_guardrail_raises_the_expected_exception_type_and_message():
    """Same request as the black-box 500 test above, but with server
    exceptions propagated to this test process directly — the precise
    proof of WHICH seam fired and WHY, not just that the request 500'd."""
    raising_client = TestClient(app, raise_server_exceptions=True)
    batch = _upload_batch(raising_client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] is not None)

    with pytest.raises(RuntimeError, match="BLOCKED by backend/tests/conftest.py"):
        raising_client.post(f"/api/person-documents/{doc['id']}/review", json={})


@pytest.mark.real_api
def test_real_api_marker_opts_out_of_the_autouse_guard(client, monkeypatch):
    """Proves the escape hatch works — WITHOUT spending anything or
    needing real credentials. @pytest.mark.real_api makes the autouse
    fixture stand down entirely for this one test; this test then patches
    the same seam itself with a controlled fake (never touching the real
    network). If the autouse guard had NOT stood down, it would have
    overwritten this test's own patch with the blocking stub, and the
    resulting request would 500 with the BLOCKED message instead of
    succeeding — so this assertion is a genuine proof the marker works,
    not a tautology.
    """
    from app.agent_client import _classify_batch_pdf  # noqa: F401 — sanity import only

    class _FakeUsage:
        api_calls = 0
        estimated_cost_usd = 0.0

    class _FakeResult:
        status = "complete"
        findings = {}
        usage = _FakeUsage()
        error = None

    monkeypatch.setattr(person_documents_module, "review_person_document", lambda *a, **k: _FakeResult())
    # This phase added a SECOND real call site (session_note_extraction.py,
    # via app.agent_client.extract_session_note_data) — with the guard
    # fixture standing down for @pytest.mark.real_api, that seam is left
    # as agent-making's REAL, unpatched function unless mocked here too;
    # without this line this test would silently attempt a real OpenRouter
    # call, contradicting its own docstring's "WITHOUT ... touching the
    # real network" claim.
    monkeypatch.setattr(
        person_documents_module, "extract_session_note_data",
        lambda *a, **k: {"fields": {}, "api_calls_used": 0, "api_cost_usd": 0.0},
    )
    # A THIRD real call site (the post-review humanize pass, via
    # app.agent_client.humanize_findings_batch) — same reasoning as
    # extract_session_note_data above. findings={} above means this
    # particular test would call it with an empty list (itself a zero-cost
    # no-op), but mocked explicitly anyway so this test stays correct even
    # if that stops being true.
    monkeypatch.setattr(
        person_documents_module, "humanize_findings_batch",
        lambda texts, **k: [(t, t, {}) for t in texts],
    )

    batch = _upload_batch(client, "batch_19page_3client.pdf")
    doc = next(d for d in batch["documents"] if d["service_code"] is not None)

    resp = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert resp.status_code == 201, resp.text
