import os
from datetime import date as date_type, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_client import extract_document_summary_fields, extract_pdf_full_text, review_person_document
from app.audit import record as audit_record
from app.db.base import get_db
from app.db.models import Person, PersonDocument, RuleResult, SessionNoteBatch, SessionNoteReview
from app.services.pdf_slicing import slice_pdf_to_temp_file
from app.services.review import compute_score_and_audit_result, finding_group, load_rules_by_id

router = APIRouter(tags=["person_documents"])


class ReviewRequestIn(BaseModel):
    actor: str | None = None  # who triggered this review run (audit actor)
    model_override: str | None = None  # None = real Anthropic path; anything else = agent-making's dev/OpenRouter path


class RuleResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rule_id: str
    check_type: str
    group: str
    final_status: str
    final_finding: str | None
    final_pages: object | None
    model_status: str | None
    is_overridden: bool


class ReviewOut(BaseModel):
    id: str
    person_document_id: str
    batch_id: str
    person_id: str | None
    client_name: str | None
    service_code: str | None
    date_of_service: date_type | None
    appendix: str | None
    reviewed: bool
    reviewed_by: str | None
    reviewed_at: datetime | None
    score: float | None
    audit_result: str
    provider_name: str | None
    bcba_name: str | None
    session_start_time: str | None
    session_end_time: str | None
    api_calls_used: int
    spend_usd: float
    grouped_results: dict[str, list[RuleResultOut]]


def _rule_result_out(rr: RuleResult, rules_by_id: dict) -> RuleResultOut:
    return RuleResultOut(
        id=rr.id, rule_id=rr.rule_id, check_type=rr.check_type,
        group=finding_group(rr, rules_by_id),
        final_status=rr.final_status, final_finding=rr.final_finding, final_pages=rr.final_pages,
        model_status=rr.model_status, is_overridden=rr.overridden_by is not None,
    )


def _build_grouped_results(rule_results: list[RuleResult]) -> dict[str, list[RuleResultOut]]:
    rules_by_id = load_rules_by_id()
    grouped: dict[str, list[RuleResultOut]] = {"Failed": [], "Not Applicable": [], "Informational": [], "Passed": []}
    for rr in rule_results:
        grouped[finding_group(rr, rules_by_id)].append(_rule_result_out(rr, rules_by_id))
    return grouped


def _review_out(review: SessionNoteReview, person_document: PersonDocument, person: Person | None) -> ReviewOut:
    return ReviewOut(
        id=review.id, person_document_id=review.person_document_id, batch_id=person_document.batch_id,
        person_id=person_document.person_id, client_name=person.full_name if person else None,
        service_code=person_document.service_code, date_of_service=person_document.date_of_service,
        appendix=person_document.appendix,
        reviewed=review.reviewed,
        reviewed_by=review.reviewed_by, reviewed_at=review.reviewed_at,
        score=review.score, audit_result=review.audit_result,
        provider_name=review.provider_name, bcba_name=review.bcba_name,
        session_start_time=review.session_start_time, session_end_time=review.session_end_time,
        api_calls_used=review.api_calls_used, spend_usd=review.spend_usd,
        grouped_results=_build_grouped_results(review.rule_results),
    )


def _finding_text(evidence) -> str | None:
    if isinstance(evidence, str):
        return evidence
    if isinstance(evidence, list):
        return "; ".join(f"[p{i.get('page')}] {i.get('detail')}" for i in evidence if isinstance(i, dict))
    return None


def _load_prior_extractions(db: Session, person_document: PersonDocument) -> list[dict] | None:
    """This person's OTHER, already-reviewed documents of the SAME service
    code, as plain data — agent-making stays stateless (see CLAUDE.md /
    docs/backend-agent-making-plan.md); this is the one place the backend
    actually fetches history before handing it over.
    """
    if person_document.person_id is None:
        return None
    rows = db.execute(
        select(SessionNoteReview, PersonDocument)
        .join(PersonDocument, SessionNoteReview.person_document_id == PersonDocument.id)
        .where(
            PersonDocument.person_id == person_document.person_id,
            PersonDocument.service_code == person_document.service_code,
            PersonDocument.id != person_document.id,
            SessionNoteReview.full_text.is_not(None),
        )
        # BUG FIX: must order by the document's own date_of_service, never
        # by upload recency (SessionNoteBatch.uploaded_at / PersonDocument.
        # created_at) — a batch uploaded today can contain a note dated
        # after (or before) one from a batch uploaded yesterday. The
        # original query had no ORDER BY at all (undefined row order).
        .order_by(PersonDocument.date_of_service.asc())
    ).all()
    if not rows:
        return None
    return [
        {"date_of_service": pd.date_of_service.isoformat() if pd.date_of_service else None, "full_text": review.full_text}
        for review, pd in rows
    ]


@router.post(
    "/person-documents/{person_document_id}/review",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
)
def review_document(person_document_id: str, body: ReviewRequestIn, db: Session = Depends(get_db)) -> ReviewOut:
    """Loads this Person's prior SessionNoteReview.full_text rows (same
    service code, earlier documents) as prior_extractions, calls
    review_person_document, and writes the SessionNoteReview + RuleResult
    rows in one transaction with the audit-log helper.
    """
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    if person_document.service_code is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this person_document is unresolved (no service_code) — it cannot be reviewed until its "
                   "classification is corrected",
        )

    batch = db.get(SessionNoteBatch, person_document.batch_id)
    prior_extractions = _load_prior_extractions(db, person_document)

    sliced_path = slice_pdf_to_temp_file(batch.original_pdf_path, person_document.page_start, person_document.page_end)
    try:
        full_text = extract_pdf_full_text(sliced_path)
        summary_fields = extract_document_summary_fields(sliced_path)
        result = review_person_document(
            sliced_path, person_document.service_code,
            prior_extractions=prior_extractions, model_override=body.model_override,
        )
    finally:
        os.unlink(sliced_path)

    if result.status == "error":
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"review_person_document failed: {result.error}")

    review = SessionNoteReview(
        person_document_id=person_document.id,
        full_text=full_text,
        provider_name=summary_fields["provider_name"],
        bcba_name=summary_fields["bcba_name"],
        session_start_time=summary_fields["session_start_time"],
        session_end_time=summary_fields["session_end_time"],
        audit_result="needs_review",
        api_calls_used=result.usage.api_calls,
        spend_usd=result.usage.estimated_cost_usd,
    )
    db.add(review)
    db.flush()

    rule_results: list[RuleResult] = []
    for rule_id, finding in result.findings.items():
        finding_text = _finding_text(finding.get("evidence"))
        rr = RuleResult(
            review_id=review.id,
            rule_id=rule_id,
            check_type=finding.get("check_type"),
            model_status=finding.get("result"),
            model_finding=finding_text,
            model_evidence=finding.get("evidence"),
            model_page=finding.get("page"),
            model_confidence=finding.get("confidence"),
            final_status=finding.get("result"),
            final_finding=finding_text,
            final_pages=finding.get("page"),
        )
        db.add(rr)
        rule_results.append(rr)
    db.flush()

    review.score, review.audit_result = compute_score_and_audit_result(rule_results)

    audit_record(
        db, entity_type="session_note_review", entity_id=review.id,
        changes={
            "audit_result": (None, review.audit_result),
            "score": (None, review.score),
            "person_document_id": (None, person_document.id),
        },
        actor=body.actor,
    )

    db.commit()
    db.refresh(review)
    person = db.get(Person, person_document.person_id) if person_document.person_id else None
    return _review_out(review, person_document, person)


class PersonDocumentOut(BaseModel):
    id: str
    batch_id: str
    person_id: str | None
    client_name: str | None
    service_code: str | None
    date_of_service: date_type | None
    appendix: str | None
    classification_confidence: str


@router.get("/person-documents/{person_document_id}", response_model=PersonDocumentOut)
def get_person_document(person_document_id: str, db: Session = Depends(get_db)) -> PersonDocumentOut:
    """Minimal context for a person_document that may not have a review
    yet — the audit detail page's own "not reviewed yet" empty state uses
    this instead of erroring on GET .../review's 404."""
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    person = db.get(Person, person_document.person_id) if person_document.person_id else None
    return PersonDocumentOut(
        id=person_document.id, batch_id=person_document.batch_id, person_id=person_document.person_id,
        client_name=person.full_name if person else None,
        service_code=person_document.service_code, date_of_service=person_document.date_of_service,
        appendix=person_document.appendix, classification_confidence=person_document.classification_confidence,
    )


@router.get("/person-documents/{person_document_id}/review", response_model=ReviewOut)
def get_latest_review(person_document_id: str, db: Session = Depends(get_db)) -> ReviewOut:
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    review = db.execute(
        select(SessionNoteReview)
        .where(SessionNoteReview.person_document_id == person_document_id)
        .order_by(SessionNoteReview.created_at.desc())
    ).scalars().first()
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no review yet for this person_document")
    person = db.get(Person, person_document.person_id) if person_document.person_id else None
    return _review_out(review, person_document, person)


class PersonDocumentSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    batch_id: str
    service_code: str | None
    date_of_service: date_type | None
    appendix: str | None
    classification_confidence: str
    classification_note: str | None


@router.get("/people/{person_id}/documents", response_model=list[PersonDocumentSummaryOut])
def list_person_documents(person_id: str, db: Session = Depends(get_db)) -> list[PersonDocument]:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person not found")
    return list(
        db.execute(
            select(PersonDocument).where(PersonDocument.person_id == person_id, PersonDocument.active.is_(True))
        ).scalars()
    )
