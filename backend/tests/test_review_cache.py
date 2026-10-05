"""Fix Round (2026-10-05), "non-determinism fix": Krishna reported that
uploading the literal same PDF multiple times produced different pass/fail
results on a couple of rules between runs. Investigation (see agent-making's
test_review_repeatability.py, same round) found no code bug anywhere
upstream of the real judgment-layer model call -- the flips are real LLM
sampling variance for rules sitting near a genuine judgment boundary.
Decision made (not mine to make unilaterally -- Krishna's own explicit
call): guarantee "same document -> same stored verdict" directly, via a
content-hash-keyed cache of the real result, rather than trying to make the
model itself more consistent. Mocked model boundary throughout -- zero real
spend testing this.
"""
from pathlib import Path

import app.routers.person_documents as person_documents_module
from app.db.models import ReviewCache

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


class _RealCallCounters:
    def __init__(self):
        self.extraction_calls = 0
        self.review_calls = 0
        self.humanize_calls = 0


def _mock_real_pipeline(monkeypatch, counters: _RealCallCounters):
    def _fake_extraction(*a, **k):
        counters.extraction_calls += 1
        return {"fields": {}, "api_calls_used": 1, "api_cost_usd": 0.0}

    def _fake_review(*a, **k):
        counters.review_calls += 1
        return _FakeResult(_findings())

    def _fake_humanize(texts, **k):
        counters.humanize_calls += 1
        return [(t, "humanized: " + t, {}) for t in texts]  # correct 3-tuple shape

    monkeypatch.setattr(person_documents_module, "extract_session_note_data", _fake_extraction)
    monkeypatch.setattr(person_documents_module, "review_person_document", _fake_review)
    monkeypatch.setattr(person_documents_module, "humanize_findings_batch", _fake_humanize)


def test_second_identical_review_is_served_from_cache_with_zero_real_calls(client, monkeypatch, db_session):
    counters = _RealCallCounters()
    _mock_real_pipeline(monkeypatch, counters)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    first = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert first.status_code == 201, first.text
    assert first.json()["served_from_cache"] is False
    assert counters.review_calls == 1

    second = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["served_from_cache"] is True
    assert second_body["api_calls_used"] == 0
    assert second_body["spend_usd"] == 0.0
    # NO new real calls at all on the cache-hit run.
    assert counters.review_calls == 1
    assert counters.extraction_calls == 1
    assert counters.humanize_calls == 1
    # The reconstructed finding matches the first run's real (humanized) result.
    failed_group = second_body["grouped_results"].get("Passed", [])
    assert any(r["final_finding"] == "humanized: Some real finding text." for r in failed_group)


def test_a_rules_json_change_is_not_served_from_the_stale_cache(client, monkeypatch, db_session):
    counters = _RealCallCounters()
    _mock_real_pipeline(monkeypatch, counters)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    first = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert first.status_code == 201
    assert counters.review_calls == 1

    # Simulate a real rules.json/prompt change: pipeline_version_fingerprint()
    # now returns a different value. Patched where person_documents_module
    # actually calls it (imported by name into that module), same pattern
    # every other agent_client seam in this test suite is mocked by.
    monkeypatch.setattr(person_documents_module, "pipeline_version_fingerprint", lambda: "a-different-pipeline-version")

    third = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert third.status_code == 201, third.text
    assert third.json()["served_from_cache"] is False
    # A genuinely NEW real review ran -- the stale cache entry (under the
    # OLD pipeline_version) was never matched.
    assert counters.review_calls == 2

    # Both rows are real, live rows -- no hard delete of the old one, just
    # never matched again (see ReviewCache's own docstring).
    rows = db_session.query(ReviewCache).all()
    assert len(rows) == 2
    versions = {r.pipeline_version for r in rows}
    assert "a-different-pipeline-version" in versions
    assert len(versions) == 2


def test_force_rerun_skips_the_cache_lookup_and_makes_a_real_call(client, monkeypatch, db_session):
    counters = _RealCallCounters()
    _mock_real_pipeline(monkeypatch, counters)

    batch = _upload_batch(client)
    doc = next(d for d in batch["documents"] if d["service_code"] == "97153")

    first = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert first.status_code == 201
    assert counters.review_calls == 1

    forced = client.post(f"/api/person-documents/{doc['id']}/review", json={"force_rerun": True})
    assert forced.status_code == 201, forced.text
    assert forced.json()["served_from_cache"] is False
    assert counters.review_calls == 2  # a genuine second real call, cache lookup bypassed

    # The forced run's fresh result becomes the new cached answer -- an
    # immediately-following NORMAL (non-forced) run reuses it, not a THIRD
    # real call.
    after_forced = client.post(f"/api/person-documents/{doc['id']}/review", json={})
    assert after_forced.status_code == 201
    assert after_forced.json()["served_from_cache"] is True
    assert counters.review_calls == 2  # unchanged -- still reused from cache


def test_compute_cache_key_changes_with_sibling_documents_content():
    from app.services.review_cache import compute_cache_key

    base_kwargs = dict(
        full_text="same document text", service_code="97153",
        prior_extractions=None, timesheet_rows=None, appendix=None,
    )
    key_no_siblings = compute_cache_key(sibling_documents=None, **base_kwargs)
    key_with_sibling = compute_cache_key(
        sibling_documents=[{"service_code": "97156", "full_text": "a real sibling document"}], **base_kwargs,
    )
    assert key_no_siblings != key_with_sibling


def test_compute_cache_key_is_stable_for_identical_inputs():
    from app.services.review_cache import compute_cache_key

    kwargs = dict(
        full_text="same document text", service_code="97153",
        prior_extractions=[{"date_of_service": "2026-09-01", "full_text": "prior note"}],
        timesheet_rows=[{"date": "09/24/2026", "start_time": "7:00 AM"}],
        appendix="I", sibling_documents=None,
    )
    assert compute_cache_key(**kwargs) == compute_cache_key(**kwargs)
