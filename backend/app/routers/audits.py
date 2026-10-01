from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from app.db.base import get_db
from app.db.models import Person, PersonDocument, SessionNoteBatch, SessionNoteReview

router = APIRouter(prefix="/audits", tags=["audits"])


class AuditSummaryOut(BaseModel):
    total_audits_completed: int
    total_patients_covered: int
    audits_this_week: int
    pass_rate: float | None  # None when there are no reviews yet, not 0 — an honest "no data" state


def _pass_rate(total_audits_completed: int, pass_count: int) -> float | None:
    """None (not 0.0) when nothing has been reviewed yet — "no data" and
    "everything failed" must never render as the same number."""
    if not total_audits_completed:
        return None
    return round(100.0 * pass_count / total_audits_completed, 2)


@router.get("/summary", response_model=AuditSummaryOut)
def audit_summary(db: Session = Depends(get_db)) -> AuditSummaryOut:
    """Dashboard stats — real counts/rates, not the Lovable export's
    hardcoded 1,247/86/34/91% placeholders. "Completed" means
    status="complete" specifically — a batch upload now creates
    pending/processing rows immediately (see app/services/batch_reviews.py),
    which must NOT inflate this count before they've actually finished."""
    total_audits_completed = db.execute(
        select(func.count(SessionNoteReview.id)).where(SessionNoteReview.status == "complete")
    ).scalar_one()

    total_patients_covered = db.execute(
        select(func.count(func.distinct(PersonDocument.person_id)))
        .where(PersonDocument.active.is_(True), PersonDocument.person_id.is_not(None))
    ).scalar_one()

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    audits_this_week = db.execute(
        select(func.count(SessionNoteReview.id))
        .where(SessionNoteReview.status == "complete", SessionNoteReview.created_at >= week_ago)
    ).scalar_one()

    pass_count = db.execute(
        select(func.count(SessionNoteReview.id)).where(SessionNoteReview.audit_result == "pass")
    ).scalar_one()
    pass_rate = _pass_rate(total_audits_completed, pass_count)

    return AuditSummaryOut(
        total_audits_completed=total_audits_completed,
        total_patients_covered=total_patients_covered,
        audits_this_week=audits_this_week,
        pass_rate=pass_rate,
    )


class AuditRowOut(BaseModel):
    person_document_id: str
    client: str | None
    code: str | None
    date_of_service: date | None
    provider: str | None
    bcba: str | None
    start_time: str | None
    end_time: str | None
    score: float | None
    reviewed_by: str | None
    review_status: str  # "Reviewed" | "Not Reviewed"
    audit_flags: str | None  # "PASSED" | "FAILED" | None (no review yet, or still pending/processing)
    review_id: str | None
    # The automatic pipeline's OWN progress — pending/processing/complete/
    # failed/skipped_spend_cap — a THIRD, independent axis from
    # review_status (human review) and audit_flags (pass/fail verdict).
    # None only for a PersonDocument with literally no review row at all
    # (shouldn't happen post-Phase-4, since one is created immediately on
    # upload, but a pre-existing document from before this phase could).
    processing_status: str | None
    active: bool


class AuditListOut(BaseModel):
    items: list[AuditRowOut]
    total: int
    page: int
    page_size: int


@router.get("", response_model=AuditListOut)
def list_audits(
    include_archived: bool = False,
    search: str | None = None,
    status: str | None = None,  # "PASSED" | "FAILED"
    date_from: date | None = None,
    date_to: date | None = None,
    batch_id: str | None = None,
    page: int = 1,
    page_size: int = 15,
    db: Session = Depends(get_db),
) -> AuditListOut:
    """One row per PersonDocument, joined with its LATEST SessionNoteReview
    (one query, not two round trips) — Client/Code/Date of Service/
    Provider/BCBA/Start Time/End Time/Score/Reviewed By/Review Status/
    Audit Flags, matching the real Brellium Audit Results list's columns.
    A PersonDocument with no review yet still gets a row (score/audit_flags
    null, review_status "Not Reviewed") rather than being hidden — nothing
    about "not yet reviewed" should look like "doesn't exist".

    Search/status/date filters, sort, and pagination all happen HERE, in
    one query — never "paginate first, then filter within just that
    page", which would silently hide matches that happen to fall on a
    later page. Sort is primarily by upload recency
    (SessionNoteBatch.uploaded_at descending — a real batch upload always
    has an earlier `uploaded_at` than the query time, so a genuinely NEW
    upload always sorts first regardless of what date_of_service is
    printed on the note), with date_of_service as a secondary tiebreaker
    only. BUG FIX: this used to sort by date_of_service ALONE, which ties
    whenever the same test PDF (same session dates) is re-uploaded and
    silently falls back to an arbitrary order that could put a brand new
    upload last instead of first.

    `include_archived`: the default list view excludes archived
    (PersonDocument.active=False) documents entirely; the Audits list's
    own "Show archived" toggle passes true to see them (still excludes
    anything hard-deleted via /delete-forever — those rows are just gone).

    `batch_id`: the Upload History tab's own "View Results" link — scopes
    this same list down to just one upload's documents, reusing this
    endpoint's existing filter/sort/pagination machinery rather than
    building a separate results view.
    """
    latest_review_id = (
        select(SessionNoteReview.id)
        .where(SessionNoteReview.person_document_id == PersonDocument.id)
        .order_by(SessionNoteReview.created_at.desc())
        .limit(1)
        .correlate(PersonDocument)
        .scalar_subquery()
    )
    LatestReview = aliased(SessionNoteReview)

    conditions = [PersonDocument.service_code.is_not(None)]
    if not include_archived:
        conditions.append(PersonDocument.active.is_(True))
    if search:
        conditions.append(Person.full_name.ilike(f"%{search}%"))
    if status == "PASSED":
        conditions.append(LatestReview.audit_result == "pass")
    elif status == "FAILED":
        conditions.append(and_(LatestReview.audit_result.is_not(None), LatestReview.audit_result != "pass"))
    if date_from:
        conditions.append(PersonDocument.date_of_service >= date_from)
    if date_to:
        conditions.append(PersonDocument.date_of_service <= date_to)
    if batch_id:
        conditions.append(PersonDocument.batch_id == batch_id)

    base_query = (
        select(PersonDocument, Person, LatestReview, SessionNoteBatch)
        .outerjoin(Person, PersonDocument.person_id == Person.id)
        .outerjoin(LatestReview, LatestReview.id == latest_review_id)
        .join(SessionNoteBatch, SessionNoteBatch.id == PersonDocument.batch_id)
        .where(*conditions)
    )

    total = db.execute(select(func.count()).select_from(base_query.subquery())).scalar_one()

    rows = db.execute(
        base_query
        .order_by(SessionNoteBatch.uploaded_at.desc(), PersonDocument.date_of_service.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    items = []
    for doc, person, review, _batch in rows:
        items.append(AuditRowOut(
            person_document_id=doc.id,
            client=person.full_name if person else None,
            code=doc.service_code,
            date_of_service=doc.date_of_service,
            provider=review.provider_name if review else None,
            bcba=review.bcba_name if review else None,
            start_time=review.session_start_time if review else None,
            end_time=review.session_end_time if review else None,
            score=review.score if review else None,
            reviewed_by=review.reviewed_by if review else None,
            review_status="Reviewed" if (review and review.reviewed) else "Not Reviewed",
            audit_flags=(
                ("PASSED" if review.audit_result == "pass" else "FAILED")
                if (review and review.audit_result is not None) else None
            ),
            review_id=review.id if review else None,
            processing_status=review.status if review else None,
            active=doc.active,
        ))
    return AuditListOut(items=items, total=total, page=page, page_size=page_size)
