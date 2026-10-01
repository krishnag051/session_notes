"""Auto-triggers a real review for every confidently-classified document in
a freshly-uploaded batch — the "finished product" path described in this
phase's own task: uploading a batch should review it automatically, not
require a manual per-document click. Processes ONE document at a time
(never in parallel) so real spend stays predictable, and respects a
batch-cumulative spend cap distinct from the existing per-document one
(see app/config.py's own batch_max_spend_usd docstring).

Runs as a FastAPI BackgroundTask, AFTER the POST /batches response has
already been sent — opens its OWN db session (the request's session is
already closed by then), one commit per document, so partial progress
survives even if a later document in the batch fails or the process
restarts mid-batch.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import PersonDocument, SessionNoteBatch, SessionNoteReview
from app.routers.person_documents import run_review


def create_pending_reviews_for_batch(db: Session, batch: SessionNoteBatch) -> list[SessionNoteReview]:
    """One pending SessionNoteReview row per confidently-classified
    PersonDocument in this batch, created immediately (in the SAME
    transaction as the batch/documents themselves) so the Audits list and
    Dashboard show "pending" the instant upload finishes — never an
    all-or-nothing wait for the whole batch's real reviews to complete."""
    reviews = []
    for doc in sorted(batch.documents, key=lambda d: d.page_start):
        if doc.service_code is None:
            continue  # unresolved — cannot be reviewed until its classification is corrected
        review = SessionNoteReview(person_document_id=doc.id, status="pending")
        db.add(review)
        reviews.append(review)
    db.flush()
    return reviews


def run_batch_reviews(batch_id: str) -> None:
    db = SessionLocal()
    try:
        batch = db.get(SessionNoteBatch, batch_id)
        if batch is None:
            return  # deleted between scheduling and running — nothing to do

        pending = db.execute(
            select(SessionNoteReview, PersonDocument)
            .join(PersonDocument, SessionNoteReview.person_document_id == PersonDocument.id)
            .where(PersonDocument.batch_id == batch_id, SessionNoteReview.status == "pending")
            .order_by(PersonDocument.page_start.asc())
        ).all()

        already_spent = db.execute(
            select(SessionNoteReview)
            .join(PersonDocument, SessionNoteReview.person_document_id == PersonDocument.id)
            .where(PersonDocument.batch_id == batch_id, SessionNoteReview.status == "complete")
        ).scalars().all()
        running_total = sum(r.spend_usd for r in already_spent)

        for review, person_document in pending:
            if running_total >= settings.batch_max_spend_usd:
                # Cap already hit by earlier documents in THIS batch — stop
                # starting new ones and say so plainly, rather than trying
                # and letting the per-document cap fail each one silently.
                review.status = "skipped_spend_cap"
                db.commit()
                continue

            review = run_review(
                db, review, person_document, batch,
                actor=None, model_override=None, raise_on_error=False,
            )
            if review.status == "complete":
                running_total += review.spend_usd
    finally:
        db.close()
