"""Phase 6: sort order (upload recency, not date_of_service alone) and
pagination (15/page) for GET /audits — both real bugs Krishna hit live
(see this phase's own report). Mocked model boundary throughout.
"""
from datetime import date, datetime, timedelta
from pathlib import Path

from app.db.models import Person, PersonDocument, SessionNoteBatch

FIXTURES = Path(__file__).resolve().parent.parent.parent / "agent-making" / "agent" / "tests" / "fixtures"


def _upload_batch(client, filename: str = "batch_19page_3client.pdf") -> dict:
    with open(FIXTURES / filename, "rb") as f:
        resp = client.post("/api/batches", files={"file": (filename, f, "application/pdf")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_sort_is_upload_recency_first_date_of_service_only_a_tiebreaker(client, db_session):
    """Re-uploading the SAME fixture (same date_of_service both times) is
    exactly the scenario that exposed the bug: the second, later upload
    must still sort first, even though its date_of_service ties with the
    first upload's."""
    batch1 = _upload_batch(client)
    doc1_id = next(d["id"] for d in batch1["documents"] if d["service_code"] == "97153")

    # Force batch1's uploaded_at clearly into the past so the two uploads'
    # real wall-clock timestamps can never tie or race, regardless of how
    # fast the two requests actually ran back to back.
    old_batch = db_session.get(SessionNoteBatch, batch1["id"])
    old_batch.uploaded_at = datetime.utcnow() - timedelta(days=1)
    db_session.commit()

    batch2 = _upload_batch(client)
    doc2_id = next(d["id"] for d in batch2["documents"] if d["service_code"] == "97153")

    rows = client.get("/api/audits", params={"page_size": 1000}).json()["items"]
    ids = [r["person_document_id"] for r in rows]
    # Same date_of_service on both (same fixture) -- upload recency must
    # still put the newer upload first.
    assert ids.index(doc2_id) < ids.index(doc1_id)


def _make_pagination_test_rows(db_session, n: int, *, marker: str) -> None:
    person = Person(global_key=f"pagination-test::{marker}", full_name=marker, dob=date(2000, 1, 1))
    db_session.add(person)
    db_session.flush()
    for i in range(n):
        batch = SessionNoteBatch(
            original_pdf_path="/dev/null", classification_result={"admin_noise_pages": [], "unresolved": []},
        )
        db_session.add(batch)
        db_session.flush()
        db_session.add(PersonDocument(
            batch_id=batch.id, person_id=person.id, service_code="97153",
            date_of_service=date(2026, 1, 1), appendix=None,
            page_start=1, page_end=1, classification_confidence="confident",
        ))
    db_session.commit()


def test_pagination_returns_15_per_page_and_a_correct_total(client, db_session):
    marker = "ZzzPaginationTestPerson"
    _make_pagination_test_rows(db_session, 17, marker=marker)

    page1 = client.get("/api/audits", params={"search": marker, "page": 1, "page_size": 15}).json()
    assert page1["total"] == 17
    assert len(page1["items"]) == 15
    assert page1["page"] == 1
    assert page1["page_size"] == 15

    page2 = client.get("/api/audits", params={"search": marker, "page": 2, "page_size": 15}).json()
    assert len(page2["items"]) == 2
    assert page2["total"] == 17

    # No overlap between the two pages.
    ids_page1 = {r["person_document_id"] for r in page1["items"]}
    ids_page2 = {r["person_document_id"] for r in page2["items"]}
    assert not (ids_page1 & ids_page2)


def test_pagination_composes_with_search_filter(client, db_session):
    """Filtering must narrow the total BEFORE paging — not page the whole
    table and then filter within just that page, which would silently
    hide matches sitting on a different page."""
    marker = "ZzzOnlyThreeOfMe"
    _make_pagination_test_rows(db_session, 3, marker=marker)

    resp = client.get("/api/audits", params={"search": marker, "page": 1, "page_size": 15}).json()
    assert resp["total"] == 3
    assert len(resp["items"]) == 3
    assert all(r["client"] == marker for r in resp["items"])
