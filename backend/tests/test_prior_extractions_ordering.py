"""BUG FIX verification: prior_extractions must be ordered by
PersonDocument.date_of_service ascending, never by upload/creation order.
Session notes for the same person can be uploaded out of chronological
order — today's batch might include a note dated after tomorrow's batch,
which might include an earlier one not yet gotten to.

The original query (backend/app/routers/person_documents.py::
_load_prior_extractions) had NO explicit ORDER BY at all — undefined row
order, not merely "wrong" order. Fixed with an explicit
`.order_by(PersonDocument.date_of_service.asc())`.

Worth noting explicitly: agent-making's history_comparison.build_history_context
does not itself pick "the first" or "the most recent" entry positionally —
it scans the WHOLE prior_extractions list and picks whichever single entry
is most textually similar to the current document, regardless of list
order. So today, list order doesn't change WHICH entry gets used for the
copy-paste comparison. What list order does affect (and what this test
actually verifies): the list itself must genuinely reflect chronological
history, for any future logic that DOES care about position (e.g. "compare
only against the immediately preceding session") and for anyone reading
this data structure expecting it to be date-ordered. Flagged for Krishna:
if the real requirement is "compare only against the most recent session,"
not "compare against the closest-matching one from all history,"
history_comparison.py's own algorithm needs a real design change, not just
this ordering fix.
"""
import uuid
from datetime import date

import app.routers.person_documents as person_documents_module
from app.db.models import Person, PersonDocument, SessionNoteBatch, SessionNoteReview


def _make_batch(db_session) -> SessionNoteBatch:
    batch = SessionNoteBatch(original_pdf_path="/dev/null", classification_result={"people": [], "admin_noise_pages": [], "unresolved": []})
    db_session.add(batch)
    db_session.flush()
    return batch


def _make_person(db_session) -> Person:
    person = Person(global_key=f"test_{uuid.uuid4().hex[:8]}", full_name="Test Person", dob=date(2020, 1, 1))
    db_session.add(person)
    db_session.flush()
    return person


def _make_reviewed_document(db_session, *, batch, person, date_of_service, full_text) -> PersonDocument:
    doc = PersonDocument(
        batch_id=batch.id, person_id=person.id, service_code="97153",
        date_of_service=date_of_service, appendix="I", page_start=1, page_end=1,
        classification_confidence="confident",
    )
    db_session.add(doc)
    db_session.flush()
    review = SessionNoteReview(person_document_id=doc.id, full_text=full_text, audit_result="pass")
    db_session.add(review)
    db_session.commit()
    return doc


def test_prior_extractions_ordered_by_date_of_service_not_upload_order(db_session):
    batch = _make_batch(db_session)
    person = _make_person(db_session)

    # Deliberately created OUT of chronological order: the LATER-dated
    # document is created (and would have been "uploaded") FIRST.
    later_doc = _make_reviewed_document(db_session, batch=batch, person=person, date_of_service=date(2026, 3, 15), full_text="March session narrative.")
    earlier_doc = _make_reviewed_document(db_session, batch=batch, person=person, date_of_service=date(2026, 1, 10), full_text="January session narrative.")
    middle_doc = _make_reviewed_document(db_session, batch=batch, person=person, date_of_service=date(2026, 2, 20), full_text="February session narrative.")

    # A 4th, brand-new document for the same person/service_code -- its
    # prior_extractions should list the three above, ordered by
    # date_of_service ascending, regardless of the creation order above.
    current_doc = PersonDocument(
        batch_id=batch.id, person_id=person.id, service_code="97153",
        date_of_service=date(2026, 4, 1), appendix="I", page_start=2, page_end=2,
        classification_confidence="confident",
    )
    db_session.add(current_doc)
    db_session.commit()

    prior = person_documents_module._load_prior_extractions(db_session, current_doc)

    assert prior is not None
    assert [p["date_of_service"] for p in prior] == ["2026-01-10", "2026-02-20", "2026-03-15"]
    assert [p["full_text"] for p in prior] == [
        "January session narrative.", "February session narrative.", "March session narrative.",
    ]
