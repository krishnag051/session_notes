"""The Upload History tab's own backend: GET /batches (list, newest first,
original_filename captured for real) and GET /audits?batch_id=... (scoping
the existing Audits list to one upload's own documents). Mocked model
boundary throughout, zero real spend.
"""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str = "batch_19page_3client.pdf", upload_filename: str | None = None) -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post(
            "/api/batches",
            files={"file": (upload_filename or filename, f, "application/pdf")},
            data={"skip_auto_review": "true"},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_upload_captures_the_real_original_filename(client):
    batch = _upload_batch(client, upload_filename="real session notes.pdf")
    assert batch["original_filename"] == "real session notes.pdf"
    # Never the random uuid-named file on disk -- the Upload History tab's
    # whole point is showing the real name the user uploaded.
    assert "real session notes.pdf" != batch["id"]


def test_list_batches_returns_newest_first(client):
    first = _upload_batch(client, upload_filename="first.pdf")
    second = _upload_batch(client, upload_filename="second.pdf")

    resp = client.get("/api/batches")
    assert resp.status_code == 200
    batches = resp.json()
    ids = [b["id"] for b in batches]
    # second was uploaded after first -- must sort before it.
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_list_batches_includes_review_summary_and_filename(client):
    batch = _upload_batch(client, upload_filename="a real upload.pdf")
    resp = client.get("/api/batches")
    assert resp.status_code == 200
    found = next(b for b in resp.json() if b["id"] == batch["id"])
    assert found["original_filename"] == "a real upload.pdf"
    assert "review_summary" in found
    assert found["auto_review_skipped"] is True


def test_audits_list_scoped_to_one_batch_excludes_other_batches_documents(client):
    batch_a = _upload_batch(client, upload_filename="batch_a.pdf")
    batch_b = _upload_batch(client, upload_filename="batch_b.pdf")

    resp = client.get(f"/api/audits?batch_id={batch_a['id']}")
    assert resp.status_code == 200
    body = resp.json()
    returned_batch_ids = set()
    for item in body["items"]:
        doc_resp = client.get(f"/api/person-documents/{item['person_document_id']}")
        returned_batch_ids.add(doc_resp.json()["batch_id"])
    assert returned_batch_ids == {batch_a["id"]}
    assert batch_b["id"] not in returned_batch_ids
    # Confirms the filter actually narrows results, not a no-op returning
    # everything regardless of batch_id.
    unscoped = client.get("/api/audits?page_size=100").json()
    assert unscoped["total"] > body["total"]
