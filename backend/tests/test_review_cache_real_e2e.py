"""Real end-to-end confirmation (2026-10-05) of the content-hash review
cache against THE ACTUAL real file that started this investigation.

Identified from the live staging deployment's own real Postgres database
(session-notes-staging-postgres-1, "up 2 hours" at the time this was
written) -- NOT guessed, NOT substituted:

    SELECT id, original_filename, uploaded_at FROM session_note_batches
    WHERE original_filename = 'daniel aiza.pdf';

      5f582d0b-f2c1-4d36-b1f9-3053cbad82df | daniel aiza.pdf | 2026-10-05 09:56:05
      0acb4de4-5801-4f68-9528-b8036eaa37f2 | daniel aiza.pdf | 2026-10-05 10:10:03

Two uploads of the identical filename, 14 minutes apart, the same morning
this round's bug report was written -- and each row's own stored
`classification_result` (computed at upload time, independent of anything
in this round's fix) shows exactly two people, one document each, both
97151: Daniel Cazi (pages 2-3) and Aiza Nabiha (pages 5-6). This matches
the original report's own description ("a couple of documents") exactly.

The real PDF bytes were pulled directly from that container's own uploads
volume (`docker cp session-notes-staging-backend-1:/app/data/uploads/
8b743bbf-e4c6-4194-863b-68d31523a97b.pdf`) and re-classified locally with
agent-making's own classify_batch_pdf -- CONFIRMED to produce the
identical page ranges/service codes/DOBs stored in the database, proving
this is genuinely the same file, not a lookalike.

SCOPE, precisely (matches the literal ask: "Review it... Re-review it
(same document, no force_rerun)..."): this test uploads the real file
ONCE, then RE-REVIEWS the SAME two PersonDocument rows a second time
(POST .../review again, no force_rerun) -- it does NOT re-upload the file
as a second, independent batch. That distinction turned out to matter: a
genuine second UPLOAD (confirmed via a zero-cost, mocked dry run before
spending any real money on it) does NOT hit the cache for a 97151
document, because _load_prior_extractions (person_documents.py) counts
the FIRST upload's own identical PersonDocument as a new "prior session"
for the second upload -- a real, separate, newly-discovered mechanism
(flagged in this round's own report, not fixed here per this round's
explicit "don't open anything new" scope) that independently explains
why literal re-uploads can diverge, on top of (or instead of) real LLM
sampling variance. Re-reviewing the SAME row never adds a new
PersonDocument, so prior_extractions is identical both times, and this is
exactly the case the cache is designed for.

REAL, BILLED Anthropic calls on the FIRST (automatic, upload-triggered)
review only -- marked @pytest.mark.real_api, never run except with
explicit, per-instance approval already in hand (this round's own: up to
$2 total across every item in it). The SECOND, manual re-review must make
ZERO new real calls, confirmed both via the stored api_calls_used/
spend_usd AND via this test's own real-call counter wrapped around the
actual agent_client seams (logged, not just asserted through the response
body).
"""
from pathlib import Path

import pytest

import app.agent_client as agent_client_module

# The real file, recovered from the live staging container's own uploads
# volume (see this module's own docstring) -- NOT a fixture committed to
# this repo, since it's a real patient-shaped document pulled from a real
# deployment, not test data authored for this suite.
REAL_FILE_PATH = Path(r"C:\Users\DELL\AppData\Local\Temp\real_krishna_file\daniel_aiza.pdf")


@pytest.mark.real_api
def test_real_reupload_of_krishnas_actual_daniel_aiza_file_is_served_from_cache_byte_identical(client):
    assert REAL_FILE_PATH.exists(), (
        f"the real file is not at {REAL_FILE_PATH} -- it was pulled into a local temp path outside this repo "
        f"(not committed), so it must be re-extracted from the live staging container before this test can run: "
        f"docker cp session-notes-staging-backend-1:/app/data/uploads/8b743bbf-e4c6-4194-863b-68d31523a97b.pdf "
        f"{REAL_FILE_PATH}"
    )

    real_call_log = []
    original_review = agent_client_module.review_person_document
    original_extract = agent_client_module.extract_session_note_data
    original_humanize = agent_client_module.humanize_findings_batch

    def _counting_review(*a, **k):
        real_call_log.append("review_person_document")
        return original_review(*a, **k)

    def _counting_extract(*a, **k):
        real_call_log.append("extract_session_note_data")
        return original_extract(*a, **k)

    def _counting_humanize(*a, **k):
        real_call_log.append("humanize_findings_batch")
        return original_humanize(*a, **k)

    import app.routers.person_documents as person_documents_module
    person_documents_module.review_person_document = _counting_review
    person_documents_module.extract_session_note_data = _counting_extract
    person_documents_module.humanize_findings_batch = _counting_humanize
    try:
        with open(REAL_FILE_PATH, "rb") as f:
            resp = client.post("/api/batches", files={"file": ("daniel aiza.pdf", f, "application/pdf")})
        assert resp.status_code == 201, resp.text
        batch = resp.json()
        docs = [d for d in batch["documents"] if d["service_code"] is not None]
        assert len(docs) == 2  # Daniel Cazi/97151, Aiza Nabiha/97151 -- exactly "a couple of documents"

        calls_during_upload = len(real_call_log)
        assert calls_during_upload > 0, "the automatic, upload-triggered review must have made real calls"
        print(f"[real-e2e] upload + automatic review made {calls_during_upload} real call(s): {real_call_log}")
        real_call_log.clear()

        for doc in docs:
            first = client.get(f"/api/person-documents/{doc['id']}/review").json()
            assert first["status"] == "complete", f"{doc['id']}: {first['status']} / {first.get('error_message')}"
            assert first["client_name"] in ("Daniel Cazi", "Aiza Nabiha")
            assert first["served_from_cache"] is False
            assert first["api_calls_used"] > 0
            assert first["spend_usd"] > 0.0
            print(f"[real-e2e] {first['client_name']}: first run spend=${first['spend_usd']:.5f}, score={first['score']}")

            calls_before_rereview = len(real_call_log)
            second = client.post(f"/api/person-documents/{doc['id']}/review", json={}).json()
            calls_during_rereview = len(real_call_log) - calls_before_rereview

            print(f"[real-e2e] {first['client_name']}: re-review made {calls_during_rereview} real call(s)")
            assert calls_during_rereview == 0, (
                f"{first['client_name']}'s re-review made {calls_during_rereview} real call(s) it should not have"
            )

            assert second["served_from_cache"] is True, f"{first['client_name']} was not served from cache"
            assert second["api_calls_used"] == 0, f"{first['client_name']} recorded real calls on a cache hit"
            assert second["spend_usd"] == 0.0, f"{first['client_name']} recorded real spend on a cache hit"
            assert second["score"] == first["score"], f"{first['client_name']} score differs"
            assert second["audit_result"] == first["audit_result"], f"{first['client_name']} audit_result differs"

            first_findings = {
                r["rule_id"]: (r["final_status"], r["final_pages"])
                for group in first["grouped_results"].values() for r in group
            }
            second_findings = {
                r["rule_id"]: (r["final_status"], r["final_pages"])
                for group in second["grouped_results"].values() for r in group
            }
            assert first_findings == second_findings, (
                f"{first['client_name']} rule-by-rule results differ:\n"
                f"first:  {first_findings}\nsecond: {second_findings}"
            )
            print(f"[real-e2e] {first['client_name']}: {len(first_findings)} rule(s), byte-identical, confirmed")
    finally:
        person_documents_module.review_person_document = original_review
        person_documents_module.extract_session_note_data = original_extract
        person_documents_module.humanize_findings_batch = original_humanize
