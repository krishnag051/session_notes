from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.db.base import get_db
from app.db.models import SessionNoteReview
from app.services.reviewers import get_or_create_reviewer

router = APIRouter(prefix="/session-note-reviews", tags=["session_note_reviews"])


class MarkReviewedIn(BaseModel):
    reviewed_by: str  # a name -- matches an existing Reviewer or auto-creates one (typeahead's own behavior)


class ReviewedStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reviewed: bool
    reviewed_by: str | None
    reviewed_at: datetime | None
    audit_result: str  # unchanged by this endpoint — shown so a caller can see both axes together


@router.patch("/{review_id}/mark-reviewed", response_model=ReviewedStatusOut)
def mark_reviewed(review_id: str, body: MarkReviewedIn, db: Session = Depends(get_db)) -> SessionNoteReview:
    """Sets reviewed/reviewed_by/reviewed_at ONLY — never touches
    audit_result. Review Status and Audit Flags are two independent axes
    (CLAUDE.md's own invariant) — a note can be fail-and-Reviewed,
    fail-and-Not-Reviewed, etc. `reviewed_by` auto-adds to the Reviewer
    table if it's a name that doesn't exist yet, so it autocompletes next
    time — no login/auth system, per this project's own explicit scope.
    """
    review = db.get(SessionNoteReview, review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_note_review not found")

    reviewer = get_or_create_reviewer(db, name=body.reviewed_by)

    old_reviewed, old_reviewed_by = review.reviewed, review.reviewed_by
    review.reviewed = True
    review.reviewed_by = reviewer.name
    review.reviewed_at = datetime.now(timezone.utc)
    db.flush()

    audit_record(
        db, entity_type="session_note_review", entity_id=review.id,
        changes={
            "reviewed": (old_reviewed, True),
            "reviewed_by": (old_reviewed_by, reviewer.name),
        },
        actor=reviewer.name,
    )

    db.commit()
    db.refresh(review)
    return review


@router.patch("/{review_id}/mark-unreviewed", response_model=ReviewedStatusOut)
def mark_unreviewed(review_id: str, db: Session = Depends(get_db)) -> SessionNoteReview:
    """The corresponding unmark action — clears reviewed/reviewed_by/
    reviewed_at, same independent-axis discipline as mark_reviewed above.
    Keeps whoever HAD reviewed it as the audit-log actor (the field-level
    diff itself is the record of what changed and by whom triggered it,
    not who's now credited on the row).
    """
    review = db.get(SessionNoteReview, review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_note_review not found")

    old_reviewed, old_reviewed_by, old_reviewed_at = review.reviewed, review.reviewed_by, review.reviewed_at
    actor = review.reviewed_by
    review.reviewed = False
    review.reviewed_by = None
    review.reviewed_at = None
    db.flush()

    audit_record(
        db, entity_type="session_note_review", entity_id=review.id,
        changes={
            "reviewed": (old_reviewed, False),
            "reviewed_by": (old_reviewed_by, None),
            "reviewed_at": (old_reviewed_at.isoformat() if old_reviewed_at else None, None),
        },
        actor=actor,
    )

    db.commit()
    db.refresh(review)
    return review
