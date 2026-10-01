import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.agent_client import classify_batch_pdf
from app.config import settings
from app.db.base import get_db
from app.db.models import PersonDocument, SessionNoteBatch, SessionNoteReview
from app.services.batch_reviews import create_pending_reviews_for_batch, run_batch_reviews
from app.services.people import get_or_create_person

router = APIRouter(prefix="/batches", tags=["batches"])


class PersonDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str | None
    service_code: str | None
    date_of_service: date | None
    appendix: str | None
    page_start: int
    page_end: int
    classification_confidence: str
    classification_note: str | None


class ReviewSummaryOut(BaseModel):
    pending: int
    processing: int
    complete: int
    failed: int
    skipped_spend_cap: int
    no_applicable_rules: int
    total_api_calls: int
    total_spend_usd: float


class BatchOut(BaseModel):
    id: str
    uploaded_by: str | None
    uploaded_at: datetime
    original_filename: str | None
    admin_noise_pages: list[dict]
    unresolved: list[dict]
    documents: list[PersonDocumentOut]
    review_summary: ReviewSummaryOut
    # True only when this specific upload request passed skip_auto_review=true
    # — see create_batch's own docstring. False (the normal case) means the
    # real per-document background review has already been scheduled.
    auto_review_skipped: bool


def _review_summary(db: Session, batch_id: str) -> ReviewSummaryOut:
    """The batch-level running total the task asks for: cumulative real
    spend/call count so far, and how many of this batch's documents are
    still pending/processing vs done — the LATEST review per document
    (same convention as GET /audits), so a manual re-run doesn't double-count."""
    latest_review_id = (
        select(SessionNoteReview.id)
        .where(SessionNoteReview.person_document_id == PersonDocument.id)
        .order_by(SessionNoteReview.created_at.desc())
        .limit(1)
        .correlate(PersonDocument)
        .scalar_subquery()
    )
    LatestReview = aliased(SessionNoteReview)
    reviews = db.execute(
        select(LatestReview)
        .join(PersonDocument, PersonDocument.batch_id == batch_id)
        .where(LatestReview.id == latest_review_id)
    ).scalars().all()

    counts = {"pending": 0, "processing": 0, "complete": 0, "failed": 0, "skipped_spend_cap": 0, "no_applicable_rules": 0}
    for r in reviews:
        counts[r.status] = counts.get(r.status, 0) + 1
    return ReviewSummaryOut(
        **counts,
        total_api_calls=sum(r.api_calls_used for r in reviews),
        total_spend_usd=round(sum(r.spend_usd for r in reviews), 4),
    )


def _batch_out(db: Session, batch: SessionNoteBatch) -> BatchOut:
    return BatchOut(
        id=batch.id,
        uploaded_by=batch.uploaded_by,
        uploaded_at=batch.uploaded_at,
        original_filename=batch.original_filename,
        admin_noise_pages=batch.classification_result.get("admin_noise_pages", []),
        unresolved=batch.classification_result.get("unresolved", []),
        documents=[PersonDocumentOut.model_validate(d) for d in batch.documents],
        review_summary=_review_summary(db, batch.id),
        auto_review_skipped=batch.auto_review_skipped,
    )


@router.post("", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    uploaded_by: str | None = Form(None),
    skip_auto_review: bool = Form(False),
    db: Session = Depends(get_db),
) -> BatchOut:
    """Accepts a PDF upload, classifies it (zero model calls — see
    classify_batch_pdf's own docstring), and for each person found: matches
    against an existing Person by global_key or creates a new one, then
    creates PersonDocument rows from the returned documents list. Every
    unresolved page range also gets a PersonDocument row (person_id=None,
    classification_confidence='unresolved') — nothing about a shaky
    classification is hidden. admin_noise_pages/unresolved are surfaced
    directly on the response from classification_result, kept verbatim.

    `skip_auto_review`: the actual safeguard added after a real, unplanned
    spend incident during manual verification (see this project's own
    incident memory) — a batch upload against ANY live-keyed backend
    (staging included) auto-triggers a real, billed review per document
    the instant this response is sent (see background_tasks.add_task
    below), with no confirmation step in between. Normal production usage
    from the real upload UI should keep defaulting to False (a reviewer
    uploading a real batch wants it reviewed automatically — that's the
    whole point). But ANY manual/verification/dry-run upload against a
    live-keyed backend — from a script, curl, or a test performed by a
    human or an agent outside the mocked test suite — MUST pass
    skip_auto_review=true explicitly unless a real review has been
    separately, explicitly authorized for that exact upload. Classification
    still runs in full (documents/appendices/pages are all still
    inspectable) — only the background real-API review is skipped.
    """
    storage_dir = Path(settings.upload_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    saved_path = storage_dir / f"{uuid.uuid4()}.pdf"
    saved_path.write_bytes(file.file.read())

    classification = classify_batch_pdf(str(saved_path))

    batch = SessionNoteBatch(
        uploaded_by=uploaded_by,
        original_pdf_path=str(saved_path),
        original_filename=file.filename,
        classification_result=classification,
        auto_review_skipped=skip_auto_review,
    )
    db.add(batch)
    db.flush()

    for person_data in classification["people"]:
        person = get_or_create_person(
            db,
            global_key=person_data["global_key"],
            full_name=person_data["full_name"],
            dob=date.fromisoformat(person_data["dob"]),
        )
        for doc in person_data["documents"]:
            db.add(PersonDocument(
                batch_id=batch.id,
                person_id=person.id,
                service_code=doc["service_code"],
                date_of_service=date.fromisoformat(doc["date_of_service"]),
                appendix=doc["appendix"],
                page_start=doc["page_start"],
                page_end=doc["page_end"],
                classification_confidence="confident",
                # This person's real "Activity Statement - <name>" cover
                # page (classify_batch_pdf's own page-boundary marker,
                # already computed — see PersonDocument.activity_statement_page's
                # own docstring for why this was never persisted before).
                activity_statement_page=person_data.get("cover_page"),
            ))

    for item in classification["unresolved"]:
        db.add(PersonDocument(
            batch_id=batch.id,
            person_id=None,
            service_code=None,
            date_of_service=None,
            appendix=None,
            page_start=item["page_start"],
            page_end=item["page_end"],
            classification_confidence="unresolved",
            classification_note=item.get("note"),
        ))

    db.flush()
    create_pending_reviews_for_batch(db, batch)
    db.commit()
    db.refresh(batch)

    # Scheduled AFTER the response is sent (Starlette's own BackgroundTasks
    # contract) — the upload request itself never blocks on any document's
    # real review. Processes this batch's documents one at a time, in
    # page_start order, respecting batch_max_spend_usd — see
    # app/services/batch_reviews.py.
    if not skip_auto_review:
        background_tasks.add_task(run_batch_reviews, batch.id)

    return _batch_out(db, batch)


@router.get("", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db)) -> list[BatchOut]:
    """The Upload History tab's own data source — every upload that
    successfully classified (an upload whose classify_batch_pdf call
    itself raised never reaches db.add(batch) at all today, so there is
    nothing to list for a failed one — by explicit decision, not an
    oversight: a failed upload isn't durable state yet). No auth in this
    app (see AppSidebar's own "No login required") — one single, shared
    history across everyone, same as every other list view here. Newest
    upload first.
    """
    batches = db.execute(
        select(SessionNoteBatch).order_by(SessionNoteBatch.uploaded_at.desc())
    ).scalars().all()
    return [_batch_out(db, b) for b in batches]


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> BatchOut:
    batch = db.get(SessionNoteBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="batch not found")
    return _batch_out(db, batch)
