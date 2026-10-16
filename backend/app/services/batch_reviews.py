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
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record as audit_record
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


def recover_orphaned_processing_reviews(db: Session) -> int:
    """Called once from app/main.py's own startup handler, BEFORE this
    process serves any request. A SessionNoteReview left at
    status="processing" whose own batch's reviews_active is False means
    the process that was reviewing it died mid-review (a kill, a crash, a
    deploy) — reviews_active would still be True if any genuinely live run
    still claimed that batch, since run_batch_reviews only ever releases it
    in its own `finally` block.

    This is the automatic version of the one-off manual fix performed by
    hand on 2026-10-15 (reset a single real orphaned row, confirmed safe
    because it had api_calls_used=0/spend_usd=0 — a "processing" review is
    ALWAYS safe to reset this way: by the time any real billed call could
    succeed, review.status has already moved past "processing" in
    run_review's own flow). Returns the number of rows recovered, logged by
    the caller.
    """
    orphaned = db.execute(
        select(SessionNoteReview)
        .join(PersonDocument, SessionNoteReview.person_document_id == PersonDocument.id)
        .join(SessionNoteBatch, PersonDocument.batch_id == SessionNoteBatch.id)
        .where(SessionNoteReview.status == "processing", SessionNoteBatch.reviews_active.is_(False))
    ).scalars().all()

    for review in orphaned:
        audit_record(
            db, entity_type="session_note_review", entity_id=review.id,
            changes={"status": ("processing", "pending")}, actor="backend_startup_recovery",
        )
        review.status = "pending"
    if orphaned:
        db.commit()
    return len(orphaned)


def run_batch_reviews(batch_id: str) -> None:
    """Runs every still-`pending` document for this batch, one at a time.

    Fix Round (2026-10-15), "real resume feature": the CALLER (either
    create_batch, for a fresh upload, or the new resume_batch_reviews
    endpoint) is responsible for atomically CLAIMING
    SessionNoteBatch.reviews_active=True before scheduling this function —
    never this function itself, since the resume endpoint's own
    double-trigger guard needs that claim to happen synchronously, in the
    request itself, before the response is even sent (this function only
    ever runs later, as a BackgroundTask). This function's own job is the
    other half: RELEASING that claim in a `finally` block once it's done,
    no matter how it ends — normal completion, every document hitting the
    batch cap, or an unexpected exception — so a run can never leave
    reviews_active stuck True forever (which would permanently block a
    future resume). If this process itself is killed (not merely raises)
    mid-run, reviews_active never gets released here at all — that's
    exactly the gap the backend startup recovery sweep exists for (see
    app/main.py), using reviews_active=False as the "is anything actually
    still claiming this orphaned processing row" signal.
    """
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

        # Fix Round (2026-10-05), "per-classified-set spend cap": a set
        # that hit its OWN per_document_hard_cap_usd ceiling mid-review
        # (status="cancelled_spend_cap") still spent real money before
        # stopping — that real spend must still count toward the BATCH's
        # own cumulative cap, same as a normally-"complete" set's spend
        # does. Counting only "complete" here would let a batch of many
        # sets that each partially spend right up to their own cap, then
        # cancel, spend far more real money in total than batch_max_spend_
        # usd was ever supposed to allow, since none of them individually
        # ever registered as "complete" spend.
        already_spent = db.execute(
            select(SessionNoteReview)
            .join(PersonDocument, SessionNoteReview.person_document_id == PersonDocument.id)
            .where(
                PersonDocument.batch_id == batch_id,
                SessionNoteReview.status.in_(["complete", "cancelled_spend_cap"]),
            )
        ).scalars().all()
        running_total = sum(r.spend_usd for r in already_spent)

        # Fix Round (2026-10-13), "O(n²) batch processing bug": ONE
        # PdfReader over this batch's own original PDF, opened once here
        # and shared across every document below — not re-opened/re-parsed
        # per document (see run_review's own docstring for the real,
        # measured cost this was costing: 47.75ms per page, over the
        # WHOLE batch file, repeated per document). Built lazily (only
        # when there's at least one pending document) so a batch with
        # nothing left to review never opens the file for no reason.
        pdf_reader = PdfReader(batch.original_pdf_path) if pending else None

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
                shared_pdf_reader=pdf_reader,
            )
            if review.status in ("complete", "cancelled_spend_cap"):
                running_total += review.spend_usd
    finally:
        # Released unconditionally, even if the try block above raised
        # partway through — a fresh query (not the possibly-stale `batch`
        # object from above, which may belong to a rolled-back transaction
        # after an exception) and its own commit, isolated from whatever
        # state the rest of this function's own session was left in.
        try:
            live_batch = db.get(SessionNoteBatch, batch_id)
            if live_batch is not None and live_batch.reviews_active:
                live_batch.reviews_active = False
                db.commit()
        except Exception:
            db.rollback()
        db.close()
