"""Schema matches CLAUDE.md's own invariants directly:
- Human override is paramount — every RuleResult consumer reads final_*,
  never model_*; model_* is written once, never touched again.
- A PersonDocument's classification is inspectable/correctable independently
  of rule results (classification_confidence, active flag).
- Review Status and Audit Flags are two independent fields — SessionNoteReview's
  reviewed/reviewed_by/reviewed_at is a separate axis from audit_result.
- No hard deletes — `active` flags instead (PersonDocument today; the same
  convention extends to any future soft-delete need).
- AuditLog is the one shared mutation-history mechanism, one row per
  changed FIELD, written in the same transaction as the change (see
  app/audit.py).
- Person.global_key is derived only (agent-making's own person_identity.py
  normalize()) — there is no endpoint that accepts it as raw input.

IDs are String(36) UUIDs (str(uuid4())), not a native Postgres UUID column
— deliberate: keeps the schema portable across Postgres (production) and
SQLite (this backend's own test suite, which has no real Postgres server
to run against in this environment) without dialect-specific column types.
"""
import uuid

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import relationship

from .base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Person(Base):
    __tablename__ = "people"

    id = Column(String(36), primary_key=True, default=_uuid)
    # Derived by agent-making's person_identity.global_key() — never
    # accepted as raw input from any endpoint (see routers/people.py).
    global_key = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    dob = Column(Date, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    documents = relationship("PersonDocument", back_populates="person")


class SessionNoteBatch(Base):
    __tablename__ = "session_note_batches"

    id = Column(String(36), primary_key=True, default=_uuid)
    uploaded_by = Column(String(255), nullable=True)
    uploaded_at = Column(DateTime, nullable=False, server_default=func.now())
    original_pdf_path = Column(String(1024), nullable=False)
    # Raw BatchClassificationResult JSON from classify_batch_pdf, kept
    # verbatim for audit — includes admin_noise_pages/unresolved, never
    # hidden from the API response.
    classification_result = Column(JSON, nullable=False)

    documents = relationship("PersonDocument", back_populates="batch")


class PersonDocument(Base):
    __tablename__ = "person_documents"

    id = Column(String(36), primary_key=True, default=_uuid)
    batch_id = Column(String(36), ForeignKey("session_note_batches.id"), nullable=False)
    # Nullable: an 'unresolved' row (see classification_confidence below)
    # genuinely might not have an identifiable person yet — CLAUDE.md's own
    # invariant is "flag when the classifier is unsure, don't guess," which
    # rules out inventing a Person for a page range classify_batch_pdf
    # itself couldn't resolve.
    person_id = Column(String(36), ForeignKey("people.id"), nullable=True)
    service_code = Column(String(16), nullable=True)
    date_of_service = Column(Date, nullable=True)
    appendix = Column(String(16), nullable=True)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    # 'confident' (came back in classify_batch_pdf's own people[].documents)
    # or 'unresolved' (came back in its unresolved bucket instead) — a
    # PersonDocument row is created for BOTH, so a shaky classification is
    # inspectable/correctable, never silently hidden. See routers/batches.py.
    classification_confidence = Column(String(16), nullable=False, default="confident")
    # classify_batch_pdf's own `note` explaining why this range is
    # unresolved — null for a 'confident' row.
    classification_note = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)  # no hard deletes

    batch = relationship("SessionNoteBatch", back_populates="documents")
    person = relationship("Person", back_populates="documents")
    reviews = relationship("SessionNoteReview", back_populates="person_document")


class SessionNoteReview(Base):
    __tablename__ = "session_note_reviews"

    id = Column(String(36), primary_key=True, default=_uuid)
    person_document_id = Column(String(36), ForeignKey("person_documents.id"), nullable=False)
    # session_note_extraction.py's 6-field output for this document — kept
    # so a LATER review of this same person's NEXT document can pass it in
    # as part of prior_extractions (see agent_client.py/routers/person_documents.py).
    # NOT YET POPULATED (this pass) — session_note_extraction.py is its own
    # real, billed-by-default-unless-OpenRouter model call, a genuinely
    # separate real-call surface Phase 3's own endpoint list didn't
    # explicitly request wiring in; left null and flagged rather than
    # silently added. See this phase's own report.
    extraction = Column(JSON, nullable=True)
    # Plain, zero-cost extracted page text for THIS review's own document —
    # what actually powers prior_extractions today (agent-making's
    # history_comparison.py compares full_text, not the 6-field summary
    # above). See app/agent_client.py::extract_pdf_full_text.
    full_text = Column(Text, nullable=True)
    # Plain, zero-cost structural extractions (agent-making's fields.py
    # helpers — no model call) for the Audit Results list's own columns.
    # Nullable: a field the source document genuinely doesn't print stays
    # null, never guessed at.
    provider_name = Column(String(255), nullable=True)
    bcba_name = Column(String(255), nullable=True)
    session_start_time = Column(String(64), nullable=True)
    session_end_time = Column(String(64), nullable=True)
    # Independent axis from audit_result — see CLAUDE.md's own invariant.
    reviewed = Column(Boolean, nullable=False, default=False)
    reviewed_by = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    # passed / (passed + failed) among scored findings — see
    # services/review.py::compute_score. Stored, not recomputed on every
    # list-page render.
    score = Column(Float, nullable=True)
    # A threshold on `score` (settings.audit_pass_threshold) — see
    # services/review.py::compute_audit_result. Never hand-set directly by
    # any endpoint.
    audit_result = Column(String(16), nullable=False, default="needs_review")
    api_calls_used = Column(Integer, nullable=False, default=0)
    spend_usd = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    person_document = relationship("PersonDocument", back_populates="reviews")
    rule_results = relationship("RuleResult", back_populates="review")


class RuleResult(Base):
    __tablename__ = "rule_results"

    id = Column(String(36), primary_key=True, default=_uuid)
    review_id = Column(String(36), ForeignKey("session_note_reviews.id"), nullable=False)
    rule_id = Column(String(64), nullable=False)
    check_type = Column(String(32), nullable=False)  # deterministic | judgment | not_checkable

    # Written ONCE, by the review endpoint, from agent-making's own
    # finding — never touched again by anyone, for any reason.
    model_status = Column(String(32), nullable=True)
    model_finding = Column(Text, nullable=True)
    model_evidence = Column(JSON, nullable=True)  # str, or the {page, detail} list form
    model_page = Column(JSON, nullable=True)  # int, list[int], or None
    model_confidence = Column(Float, nullable=True)

    # Every consumer reads THESE, never model_*. Defaults to a copy of the
    # model_* values at creation time; only PATCH /rule-results/{id} ever
    # changes them afterward.
    final_status = Column(String(32), nullable=False)
    final_finding = Column(Text, nullable=True)
    final_pages = Column(JSON, nullable=True)

    overridden_by = Column(String(255), nullable=True)
    overridden_at = Column(DateTime, nullable=True)
    override_reason = Column(Text, nullable=True)

    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    review = relationship("SessionNoteReview", back_populates="rule_results")


class Reviewer(Base):
    """No login/auth system — just a short, typed-into-once list backing
    the audit detail view's 'Reviewed By' typeahead. A new name typed there
    auto-adds itself here (see routers/session_note_reviews.py) so it
    autocompletes next time."""

    __tablename__ = "reviewers"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class AuditLog(Base):
    """One row per changed FIELD, written in the same transaction as the
    change it records — see app/audit.py::record. Never constructed
    directly anywhere else."""

    __tablename__ = "audit_log"

    id = Column(String(36), primary_key=True, default=_uuid)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(String(36), nullable=False)
    field = Column(String(128), nullable=False)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    actor = Column(String(255), nullable=True)  # None only for system/automated actions
    at = Column(DateTime, nullable=False, server_default=func.now())
