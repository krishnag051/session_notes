from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Reviewer
from app.services.reviewers import get_or_create_reviewer

router = APIRouter(prefix="/reviewers", tags=["reviewers"])


class ReviewerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str | None


class ReviewerIn(BaseModel):
    name: str
    email: str | None = None


@router.get("", response_model=list[ReviewerOut])
def list_reviewers(q: str | None = None, db: Session = Depends(get_db)) -> list[Reviewer]:
    """The typeahead's own data source — a short, plain list (no login/
    auth), managed from a Settings page and auto-added-to by mark-reviewed
    (see routers/session_note_reviews.py). `q` is an optional prefix filter
    for server-side narrowing; the frontend may also just fetch the full
    list once and filter client-side, since this list is expected to stay
    small.
    """
    stmt = select(Reviewer).order_by(Reviewer.name.asc())
    if q:
        stmt = stmt.where(Reviewer.name.ilike(f"{q}%"))
    return list(db.execute(stmt).scalars())


@router.post("", response_model=ReviewerOut, status_code=status.HTTP_201_CREATED)
def create_reviewer(body: ReviewerIn, db: Session = Depends(get_db)) -> Reviewer:
    """The Settings page's own explicit 'add reviewer' form — same get-or-
    create behavior the typeahead's auto-add already uses, so typing an
    existing name here never creates a duplicate."""
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    reviewer = get_or_create_reviewer(db, name=body.name, email=body.email)
    db.commit()
    db.refresh(reviewer)
    return reviewer
