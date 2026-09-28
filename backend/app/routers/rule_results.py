from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.db.base import get_db
from app.db.models import RuleResult, SessionNoteReview
from app.services.review import compute_score_and_audit_result, load_rules_by_id

router = APIRouter(prefix="/rule-results", tags=["rule_results"])

RuleResultStatus = Literal["pass", "fail", "uncertain", "not_applicable", "not_checkable"]


class RuleResultPatch(BaseModel):
    final_status: RuleResultStatus | None = None
    final_finding: str | None = None
    final_pages: object | None = None
    override_reason: str
    actor: str


class RuleResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rule_id: str
    final_status: str
    final_finding: str | None
    final_pages: object | None
    overridden_by: str | None
    overridden_at: datetime | None
    override_reason: str | None


@router.patch("/{rule_result_id}", response_model=RuleResultOut)
def override_rule_result(rule_result_id: str, body: RuleResultPatch, db: Session = Depends(get_db)) -> RuleResult:
    """Human override — sets final_status/final_finding/final_pages,
    overridden_by/overridden_at/override_reason; NEVER touches model_*
    (CLAUDE.md's own invariant: model_* is written once by the pipeline
    and never updated again, by anyone, for any reason). Writes a
    field-level audit-log diff in the same transaction as the change.
    """
    rule_result = db.get(RuleResult, rule_result_id)
    if rule_result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="rule_result not found")

    requested = body.model_dump(exclude_unset=True, exclude={"override_reason", "actor"})
    if not requested:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no field to override was provided")

    changes = {}
    for field, new_value in requested.items():
        old_value = getattr(rule_result, field)
        if old_value != new_value:
            changes[field] = (old_value, new_value)
        setattr(rule_result, field, new_value)

    now = datetime.now(timezone.utc)
    rule_result.overridden_by = body.actor
    rule_result.overridden_at = now
    rule_result.override_reason = body.override_reason
    changes["overridden_by"] = (None, body.actor)
    changes["override_reason"] = (None, body.override_reason)

    db.flush()

    audit_record(db, entity_type="rule_result", entity_id=rule_result.id, changes=changes, actor=body.actor)

    # A human override can change the whole review's score/audit_result
    # (e.g. overriding a fail to a pass) — recompute here rather than
    # leaving it stale until the next full review.
    review = db.get(SessionNoteReview, rule_result.review_id)
    rules_by_id = load_rules_by_id()
    new_score, new_audit_result = compute_score_and_audit_result(review.rule_results, rules_by_id)
    if new_score != review.score or new_audit_result != review.audit_result:
        audit_record(
            db, entity_type="session_note_review", entity_id=review.id,
            changes={
                "score": (review.score, new_score),
                "audit_result": (review.audit_result, new_audit_result),
            },
            actor=body.actor,
        )
        review.score = new_score
        review.audit_result = new_audit_result

    db.commit()
    db.refresh(rule_result)
    return rule_result
