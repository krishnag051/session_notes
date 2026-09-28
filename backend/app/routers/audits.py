from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.db.base import get_db
from app.db.models import Person, PersonDocument, SessionNoteReview

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
    hardcoded 1,247/86/34/91% placeholders."""
    total_audits_completed = db.execute(select(func.count(SessionNoteReview.id))).scalar_one()

    total_patients_covered = db.execute(
        select(func.count(func.distinct(PersonDocument.person_id)))
        .where(PersonDocument.active.is_(True), PersonDocument.person_id.is_not(None))
    ).scalar_one()

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    audits_this_week = db.execute(
        select(func.count(SessionNoteReview.id)).where(SessionNoteReview.created_at >= week_ago)
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
    audit_flags: str | None  # "PASSED" | "FAILED" | None (no review yet)
    review_id: str | None


@router.get("", response_model=list[AuditRowOut])
def list_audits(db: Session = Depends(get_db)) -> list[AuditRowOut]:
    """One row per PersonDocument, joined with its LATEST SessionNoteReview
    (one query, not two round trips) — Client/Code/Date of Service/
    Provider/BCBA/Start Time/End Time/Score/Reviewed By/Review Status/
    Audit Flags, matching the real Brellium Audit Results list's columns.
    A PersonDocument with no review yet still gets a row (score/audit_flags
    null, review_status "Not Reviewed") rather than being hidden — nothing
    about "not yet reviewed" should look like "doesn't exist".
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

    rows = db.execute(
        select(PersonDocument, Person, LatestReview)
        .outerjoin(Person, PersonDocument.person_id == Person.id)
        .outerjoin(LatestReview, LatestReview.id == latest_review_id)
        .where(PersonDocument.active.is_(True), PersonDocument.service_code.is_not(None))
        .order_by(PersonDocument.date_of_service.desc())
    ).all()

    out = []
    for doc, person, review in rows:
        out.append(AuditRowOut(
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
                if review else None
            ),
            review_id=review.id if review else None,
        ))
    return out
