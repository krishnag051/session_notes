from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Person

router = APIRouter(prefix="/people", tags=["people"])


class MergePersonIn(BaseModel):
    merge_into_person_id: str
    reason: str
    actor: str


@router.post("/{person_id}/merge")
def merge_person(person_id: str, body: MergePersonIn, db: Session = Depends(get_db)):
    """STUB — a misclassified/duplicate Person (the Shaya/Shea Herskovic
    same-DOB scenario) needs an explicit merge operation with its own audit
    trail, per CLAUDE.md's invariant: Person.global_key is derived, never
    hand-editable, so "this was actually the same person" can't be fixed
    by editing a row — it has to be its own tracked operation.

    NOT IMPLEMENTED YET. Real behavior this needs to decide before being
    built for real (do not guess at these — flag and ask, per CLAUDE.md):
    - What happens to the "losing" Person's own PersonDocument/
      SessionNoteReview rows — re-pointed to the surviving Person, or kept
      under the old one with a redirect?
    - Does a merge need to be reversible (a "split" counterpart), and if
      so, what does undoing it require?
    - Whose audit trail owns this: a new "person_merge" AuditLog entity_type,
      or field-level diffs against both Person rows?
    This endpoint exists so the schema has somewhere for that decision to
    live once made — not to preempt the decision itself.
    """
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person not found")
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="POST /people/{id}/merge is not implemented yet — see this endpoint's own docstring for what's undecided.",
    )
