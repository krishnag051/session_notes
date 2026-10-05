import logging
import os
import time
import traceback
from datetime import date as date_type, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_client import (
    SPEND_CAP_EXCEEDED_EXCEPTIONS,
    extract_activity_statement_rows,
    extract_data_points,
    extract_document_summary_fields,
    extract_pdf_full_text,
    extract_session_note_data,
    humanize_findings_batch,
    pipeline_version_fingerprint,
    review_person_document,
)
from app.audit import record as audit_record
from app.config import settings
from app.db.base import get_db
from app.db.models import Person, PersonDocument, RuleResult, SessionNoteBatch, SessionNoteReview
from app.services import review_cache as review_cache_module
from app.services.pdf_slicing import slice_pdf_to_temp_file
from app.services.review import compute_score_and_audit_result, finding_group, has_applicable_rules, load_rules_by_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["person_documents"])


class ReviewRequestIn(BaseModel):
    actor: str | None = None  # who triggered this review run (audit actor)
    model_override: str | None = None  # None = real Anthropic path; anything else = agent-making's dev/OpenRouter path
    # Fix Round (2026-10-05), "non-determinism fix": true deliberately
    # bypasses the review-result cache lookup (see run_review's own
    # docstring) — a reviewer re-evaluating a document on their own
    # judgment, not blocked behind a rules/prompt change. Still a REAL,
    # billed re-run; still writes its fresh result into the cache
    # afterward.
    force_rerun: bool = False


class RuleResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    rule_id: str
    question: str | None  # rules.json's own description — the findings panel's bold header text
    check_type: str
    group: str
    final_status: str
    final_finding: str | None
    final_pages: object | None
    model_status: str | None
    # The real, pre-humanization reasoning text — see RuleResult.model_finding_raw's
    # own docstring. Surfaced for the CSV export's "raw vs humanized" columns.
    model_finding_raw: str | None
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
    has_activity_statement: bool
    reviewed: bool
    reviewed_by: str | None
    reviewed_at: datetime | None
    status: str
    error_message: str | None
    score: float | None
    audit_result: str | None
    provider_name: str | None
    bcba_name: str | None
    session_start_time: str | None
    session_end_time: str | None
    api_calls_used: int
    spend_usd: float
    # Fix Round (2026-10-05), "non-determinism fix": true when this
    # review's results were reused from an identical prior run rather than
    # computed fresh (api_calls_used/spend_usd are 0 in that case) — see
    # app/services/review_cache.py.
    served_from_cache: bool
    grouped_results: dict[str, list[RuleResultOut]]


def _rule_result_out(rr: RuleResult, rules_by_id: dict) -> RuleResultOut:
    return RuleResultOut(
        id=rr.id, rule_id=rr.rule_id,
        question=rules_by_id.get(rr.rule_id, {}).get("description"),
        check_type=rr.check_type,
        group=finding_group(rr, rules_by_id),
        final_status=rr.final_status, final_finding=rr.final_finding, final_pages=rr.final_pages,
        model_status=rr.model_status, model_finding_raw=rr.model_finding_raw,
        is_overridden=rr.overridden_by is not None,
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
        has_activity_statement=person_document.activity_statement_page is not None,
        reviewed=review.reviewed,
        reviewed_by=review.reviewed_by, reviewed_at=review.reviewed_at,
        status=review.status, error_message=review.error_message,
        score=review.score, audit_result=review.audit_result,
        provider_name=review.provider_name, bcba_name=review.bcba_name,
        session_start_time=review.session_start_time, session_end_time=review.session_end_time,
        api_calls_used=review.api_calls_used, spend_usd=review.spend_usd,
        served_from_cache=review.served_from_cache,
        grouped_results=_build_grouped_results(review.rule_results),
    )


def _finding_text(evidence) -> str | None:
    if isinstance(evidence, str):
        return evidence
    if isinstance(evidence, list):
        return "; ".join(f"[p{i.get('page')}] {i.get('detail')}" for i in evidence if isinstance(i, dict))
    return None


def _load_prior_extractions(
    db: Session, person_document: PersonDocument, *, current_full_text: str | None = None,
) -> list[dict] | None:
    """This person's OTHER, already-reviewed documents of the SAME service
    code, as plain data — agent-making stays stateless (see CLAUDE.md /
    docs/backend-agent-making-plan.md); this is the one place the backend
    actually fetches history before handing it over.

    REAL BUG FOUND AND FIXED (2026-10-05, "cross-upload history leak"):
    the `active=True` filter below already excludes an ARCHIVED duplicate
    of this same real visit (see that filter's own comment — a real
    Joseph Bergstein 97153 CSV audit caught that one), but it does NOT
    exclude a SECOND, still-ACTIVE upload of the byte-identical document —
    confirmed real: re-uploading Krishna's own real "daniel aiza.pdf" a
    second time made the FIRST upload's own Daniel Cazi document count as
    a genuine "prior session" for the second upload's own Daniel Cazi
    document, even though they are the exact same real visit, re-processed
    twice. A needs_history rule (e.g. "does this narrative duplicate a
    prior note word-for-word?") then compares a document against an
    archived-in-spirit copy of ITSELF and could flag a false positive, or
    a history-trend rule could silently double-count one real visit as
    two. This is a correctness bug independent of (and in addition to) the
    real LLM judgment-sampling variance fixed earlier the same round — it
    affects any needs_history rule, not just the one that happened to
    surface it.

    Fixed by excluding any candidate row whose OWN stored full_text is
    byte-identical to `current_full_text` — content identity, not upload
    timestamp/batch/id, is what actually makes two rows "the same real
    visit" vs. "two real, separate sessions that happen to share a date."
    Exact string equality is deliberate, not a hash: extraction is already
    confirmed deterministic for identical PDF bytes (see agent-making's
    own test_review_repeatability.py), so a genuine re-upload of the same
    document always produces byte-identical full_text, making this
    precise rather than approximate. `current_full_text=None` (a caller
    that hasn't extracted it yet) disables this specific check — the
    archived-duplicate and basic recency filters below still apply.
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
            # REAL BUG FOUND AND FIXED: without this, an ARCHIVED duplicate
            # of THIS SAME real visit (the same source PDF re-uploaded in a
            # separate batch — confirmed via a real Joseph Bergstein 97153
            # CSV audit: two PersonDocument rows, same person+service_code+
            # date_of_service, one `active=False`) still counted as a
            # genuine "prior session" — comparing this document against an
            # archived copy of ITSELF, not a real separate visit, and
            # producing a false "100% similarity to a previous session".
            # `active=False` is CLAUDE.md's own explicit "this doesn't
            # represent current, real data" flag — history comparison must
            # never pull from it.
            PersonDocument.active.is_(True),
            SessionNoteReview.full_text.is_not(None),
        )
        # BUG FIX: must order by the document's own date_of_service, never
        # by upload recency (SessionNoteBatch.uploaded_at / PersonDocument.
        # created_at) — a batch uploaded today can contain a note dated
        # after (or before) one from a batch uploaded yesterday. The
        # original query had no ORDER BY at all (undefined row order).
        .order_by(PersonDocument.date_of_service.asc())
    ).all()
    results = [
        {"date_of_service": pd.date_of_service.isoformat() if pd.date_of_service else None, "full_text": review.full_text}
        for review, pd in rows
        if current_full_text is None or review.full_text != current_full_text
    ]
    return results or None


def _load_sibling_documents(db: Session, batch: SessionNoteBatch, person_document: PersonDocument) -> list[dict] | None:
    """This SAME person's OTHER documents from the SAME batch/upload (any
    service code), as plain data — for any rule phrased as "at least one
    session note" rather than scoped to a specific assessment/service type
    (see agent-making's fields.py::_check_SN_97151_11). Real bug found via
    a real Daniel Cazi 97151/97156 CSV audit: a sibling 97156 (Parent
    Training) document in the same upload already covered this rule's own
    question, but the pipeline only ever looked at the single document
    being reviewed. Extracted fresh (zero-cost, no model call) from each
    sibling's own page range — NOT read from a previously-stored review's
    full_text, since batch review processes one document at a time and a
    later sibling may not have been reviewed yet when an earlier one is.

    CRITICAL BUG FOUND AND FIXED (real, confirmed cross-patient data leak):
    a batch upload is NOT one person per batch — a single real upload
    (confirmed directly: one real 14-document, multi-patient batch_id)
    can and does contain MANY different patients' documents together. This
    query originally filtered ONLY by batch_id, with no person_id filter
    at all — so a person with NO real sibling of their own (confirmed:
    Aiza Nabiha, read page-by-page, has none) was being handed ANOTHER
    REAL PATIENT'S document (Daniel Cazi's own real 97156 document,
    sitting in the same batch_id) as if it were her own "sibling",
    producing PASS with Cazi's own real content/reasoning attached to her
    review. `PersonDocument.person_id == person_document.person_id` is the
    fix — sibling lookups must be scoped to the same PATIENT, not just the
    same batch row.
    """
    siblings = db.execute(
        select(PersonDocument).where(
            PersonDocument.batch_id == batch.id,
            PersonDocument.person_id == person_document.person_id,
            PersonDocument.id != person_document.id,
            PersonDocument.active.is_(True),
            PersonDocument.service_code.is_not(None),
        )
    ).scalars().all()
    if not siblings:
        return None
    result = []
    for sibling in siblings:
        sib_path = slice_pdf_to_temp_file(batch.original_pdf_path, sibling.page_start, sibling.page_end)
        try:
            result.append({"service_code": sibling.service_code, "full_text": extract_pdf_full_text(sib_path)})
        finally:
            os.unlink(sib_path)
    return result


def run_review(
    db: Session,
    review: SessionNoteReview,
    person_document: PersonDocument,
    batch: SessionNoteBatch,
    *,
    actor: str | None = None,
    model_override: str | None = None,
    raise_on_error: bool = True,
    force_rerun: bool = False,
) -> SessionNoteReview:
    """Runs the real pipeline for an EXISTING (already flushed) review row —
    the one place both the manual re-run endpoint below and the automatic
    per-batch background job (app/services/batch_reviews.py) actually call
    into agent-making. Mutates `review` in place (status -> processing,
    then complete/failed) and commits — caller is responsible for the
    row's initial creation/flush and for the batch-level spend-cap check
    (this function always runs once it's called).

    `raise_on_error`: the batch background job (no HTTP response to return,
    other documents in the same batch still need to run) always passes
    False — one document's failure must never crash the whole batch, so it
    marks status="failed" with the real error message and moves on. The
    manual re-run endpoint keeps the ORIGINAL pre-this-phase behavior
    (True): an unexpected exception still surfaces as a real 500, not a
    silently-swallowed "failed" row — this is also what
    tests/test_real_api_guardrail.py's own proof relies on (an unmocked
    call must be LOUD, never quietly absorbed).

    HARD PER-DOCUMENT SPEND CAP (urgent production ask, settings.
    per_document_hard_cap_usd, default $2.00): enforced by giving EACH of
    the three real call stages below (extraction, review_person_document,
    humanize) only its own REMAINING headroom under the cap as ITS OWN
    max_spend_usd — never the full cap, and never looser than this
    function's own existing per-stage settings (rule_engine_max_spend_usd
    etc.), only ever tighter. Once accumulated_cost_usd already meets or
    exceeds the cap, the next stage is skipped outright (remaining
    headroom computed as exactly $0, which every stage's own tracker
    already refuses to spend anything against) rather than attempted and
    left to fail on its own. This makes it structurally impossible for
    the three stages combined to exceed the cap, regardless of which one
    would otherwise have been the big spender.

    CACHING (Fix Round 2026-10-05, "non-determinism fix"): once the
    zero-cost setup below (full_text/summary_fields/timesheet_rows) is
    available, this document's own cache key is computed (see
    app/services/review_cache.py::compute_cache_key) and looked up against
    the CURRENT pipeline_version_fingerprint(). A hit reconstructs this
    review's RuleResult rows directly from the cached payload — zero real
    calls, api_calls_used/spend_usd=0, served_from_cache=True — and skips
    the entire retry loop/real pipeline below. `force_rerun=True` skips
    the LOOKUP only (a reviewer's deliberate "re-evaluate this on its own
    merits" action); the real run that follows still WRITES its fresh
    result into the cache afterward, becoming the new cached answer for
    future automatic lookups of this same (cache_key, pipeline_version).
    """
    review.status = "processing"
    db.commit()

    sibling_documents = _load_sibling_documents(db, batch, person_document)
    sliced_path = slice_pdf_to_temp_file(batch.original_pdf_path, person_document.page_start, person_document.page_end)

    try:
        full_text = extract_pdf_full_text(sliced_path)
        summary_fields = extract_document_summary_fields(sliced_path)
        review.full_text = full_text
        # Moved here (was before full_text existed) so the "exclude a
        # byte-identical re-upload from this document's own prior-session
        # pool" check (see this function's own docstring, "cross-upload
        # history leak") has the current document's real text to compare
        # against — a no-op difference for the SPEND-cap-irrelevant
        # zero-cost query ordering, not a stage reordering of anything billed.
        prior_extractions = _load_prior_extractions(db, person_document, current_full_text=full_text)
        review.provider_name = summary_fields["provider_name"]
        review.bcba_name = summary_fields["bcba_name"]
        review.session_start_time = summary_fields["session_start_time"]
        review.session_end_time = summary_fields["session_end_time"]

        # BUG FIX: a document whose service_code has NO rules.json entries
        # at all (97156 today — see has_applicable_rules's own docstring)
        # used to still run the full real pipeline anyway, burning real
        # spend for zero checking value and landing on a misleading 100%
        # ("nothing to fail") score. Checked here, BEFORE either real call
        # below, using only the free full_text/summary_fields work above —
        # never even queues the billed calls for a service_code with
        # nothing to check.
        if not has_applicable_rules(person_document.service_code):
            review.status = "no_applicable_rules"
            db.commit()
            os.unlink(sliced_path)
            return review

        # Zero-cost — same Activity Statement parse every attempt would
        # otherwise redo inside the retry loop; computed once, up front,
        # both for the real pipeline call below AND for the cache key
        # (this is a real input to the rule-checking pipeline — the SAME
        # document with a DIFFERENT timesheet row attached is a genuinely
        # different review, so it must be part of "same document").
        timesheet_rows = (
            extract_activity_statement_rows(batch.original_pdf_path, person_document.activity_statement_page)
            if person_document.activity_statement_page is not None
            else None
        )

        cache_key = review_cache_module.compute_cache_key(
            full_text=full_text, service_code=person_document.service_code,
            prior_extractions=prior_extractions, timesheet_rows=timesheet_rows,
            appendix=person_document.appendix, sibling_documents=sibling_documents,
        )
        pipeline_version = pipeline_version_fingerprint()

        if not force_rerun:
            cached = review_cache_module.lookup(db, cache_key=cache_key, pipeline_version=pipeline_version)
            if cached is not None:
                review_cache_module.record_hit(db, cached)
                rule_results = [
                    RuleResult(
                        review_id=review.id, rule_id=rule_id,
                        check_type=f["check_type"], model_status=f["model_status"],
                        model_finding=f["model_finding"], model_finding_raw=f["model_finding_raw"],
                        model_evidence=f["model_evidence"], model_page=f["model_page"],
                        model_confidence=f["model_confidence"],
                        final_status=f["model_status"], final_finding=f["model_finding"],
                        final_pages=f["model_page"],
                    )
                    for rule_id, f in cached.payload["findings"].items()
                ]
                for rr in rule_results:
                    db.add(rr)
                db.flush()
                review.extraction = cached.payload["review_extraction"]
                review.score, review.audit_result = compute_score_and_audit_result(rule_results)
                review.api_calls_used = 0
                review.spend_usd = 0.0
                review.served_from_cache = True
                review.status = "complete"
                audit_record(
                    db, entity_type="session_note_review", entity_id=review.id,
                    changes={
                        "status": ("processing", "complete"),
                        "audit_result": (None, review.audit_result),
                        "score": (None, review.score),
                        "served_from_cache": (False, True),
                    },
                    actor=actor,
                )
                db.commit()
                db.refresh(review)
                os.unlink(sliced_path)
                return review

        db.commit()
    except Exception:  # noqa: BLE001 — this zero-cost setup phase has no real spend to lose; not worth retrying
        review.status = "failed"
        review.error_message = traceback.format_exc()
        logger.error(
            "review setup failed for person_document_id=%s: %s", person_document.id, review.error_message,
        )
        db.commit()
        os.unlink(sliced_path)
        if raise_on_error:
            raise
        return review

    # A transient failure (a flaky real-API response, a momentary network
    # blip) shouldn't require a human to notice and manually re-trigger
    # this document — bounded automatic retries for an UNEXPECTED
    # exception only (never for review_person_document's own structured
    # status="error" reporting below, e.g. a spend cap — retrying that
    # wouldn't help and would just burn more real calls for the same
    # outcome). accumulated_cost_usd/accumulated_calls span every attempt,
    # not just the last one, so a failed-then-retried-successfully attempt
    # still accounts for whatever an earlier attempt's extraction call
    # really cost. No exception ever escapes this loop — it always either
    # breaks on success or falls through having recorded last_traceback,
    # and the raise-on-exhaustion (if any) happens cleanly AFTER the loop,
    # never from inside it, so it can't be caught by its own except clause.
    max_attempts = 1 + settings.review_retry_attempts
    accumulated_cost_usd = 0.0
    accumulated_calls = 0
    extraction: dict | None = None
    result = None
    last_traceback: str | None = None
    # Fix Round (2026-10-05), "per-classified-set spend cap": true only
    # when a SPEND_CAP_EXCEEDED_EXCEPTIONS was actually raised directly
    # from extract_session_note_data (the one real call site in this loop
    # that can raise one uncaught, rather than reporting it through
    # review_person_document's own structured status="error" return) —
    # decides status="cancelled_spend_cap" vs status="failed" below.
    cap_exceeded_hit = False

    for attempt in range(1, max_attempts + 1):
        try:
            remaining_budget = max(0.0, settings.per_document_hard_cap_usd - accumulated_cost_usd)
            extraction = extract_session_note_data(
                sliced_path, model_override=model_override, max_spend_usd=remaining_budget,
            )
            accumulated_cost_usd += extraction["api_cost_usd"]
            accumulated_calls += extraction["api_calls_used"]
            data_points = extract_data_points(sliced_path)
            review.extraction = {"fields": extraction["fields"], "data_points": data_points}

            # timesheet_rows already computed once, above, before the
            # cache-key/lookup — same Activity Statement data every retry
            # attempt would otherwise recompute identically.
            remaining_budget = max(0.0, settings.per_document_hard_cap_usd - accumulated_cost_usd)
            result = review_person_document(
                sliced_path, person_document.service_code,
                prior_extractions=prior_extractions, model_override=model_override,
                timesheet_rows=timesheet_rows, appendix=person_document.appendix,
                sibling_documents=sibling_documents, max_spend_usd=remaining_budget,
            )
            # REAL BUG FOUND AND FIXED: review_person_document's own
            # blanket exception handler (agent-making/agent/pipeline/
            # api.py) never lets a raw exception escape — it catches
            # EVERYTHING and returns status="error" instead (this is where
            # the original PydanticUserError crash actually landed, NOT as
            # a raised Python exception reaching this function at all, so
            # the retry loop below never used to retry it). error_type
            # distinguishes a deliberate, non-retryable limit
            # ("cap_exceeded") from a genuinely unexpected one-off crash
            # ("unexpected") — only the latter retries here; a spend/call
            # cap being hit again on retry would just waste another real
            # call for the identical outcome.
            if result.status == "error" and result.error_type == "unexpected":
                last_traceback = result.error
                accumulated_cost_usd += result.usage.estimated_cost_usd
                accumulated_calls += result.usage.api_calls
                logger.error(
                    "review attempt %d/%d failed for person_document_id=%s (unexpected error from "
                    "review_person_document itself): %s",
                    attempt, max_attempts, person_document.id, last_traceback,
                )
                result = None
                if attempt < max_attempts:
                    time.sleep(settings.review_retry_backoff_seconds)
                    continue
                break
            break  # success, or a deliberate non-retryable error — stop retrying
        except SPEND_CAP_EXCEEDED_EXCEPTIONS as exc:
            # Fix Round (2026-10-05), "per-classified-set spend cap": this
            # ONE set's own per_document_hard_cap_usd ceiling was hit mid-
            # review (extract_session_note_data raised directly — unlike
            # review_person_document, which never lets this escape as a
            # raised exception, see the structured status="error" branch
            # above). Never retried — the next attempt would recompute an
            # identical ~$0 remaining budget and hit the exact same cap
            # immediately, burning a retry/backoff cycle for a guaranteed
            # repeat of the same outcome. Stops this set here, cleanly.
            last_traceback = str(exc)
            cap_exceeded_hit = True
            logger.warning(
                "person_document_id=%s hit its per-classified-set spend cap mid-review "
                "(accumulated so far: $%.4f): %s",
                person_document.id, accumulated_cost_usd, last_traceback,
            )
            break
        except Exception:  # noqa: BLE001 — an honest "failed" status, never a bare 500 mid-batch
            # The FULL traceback, not just str(exc) — the previous,
            # message-only form made a real production crash
            # (PydanticUserError: "BaseModel cannot be instantiated
            # directly") undiagnosable after the fact, with no file/line
            # pointing at the actual call site. Logged immediately too, so
            # it's visible even if this review's own row is never inspected.
            last_traceback = traceback.format_exc()
            result = None
            logger.error(
                "review attempt %d/%d failed for person_document_id=%s: %s",
                attempt, max_attempts, person_document.id, last_traceback,
            )
            if attempt < max_attempts:
                time.sleep(settings.review_retry_backoff_seconds)

    if cap_exceeded_hit:
        # Distinct from status="failed" on purpose (see SessionNoteReview.
        # status's own docstring) — a deliberate, expected safety stop,
        # never a bug. Other sets in the same batch are entirely
        # unaffected: batch_reviews.py calls run_review once per document,
        # in its own try/except-free loop iteration — this return here
        # never raises past this function (raise_on_error=True callers are
        # the manual endpoint only, where a 4xx-shaped response, not a
        # retry, is the right reaction to a deliberate cap trip).
        review.status = "cancelled_spend_cap"
        review.error_message = last_traceback
        review.spend_usd = accumulated_cost_usd
        review.api_calls_used = accumulated_calls
        db.commit()
        os.unlink(sliced_path)
        return review

    if result is None:
        # Every attempt either raised or came back as an unexpected error,
        # and retries are exhausted — a clear, permanent failed state,
        # never a silent loop or hang.
        review.status = "failed"
        review.error_message = last_traceback
        review.spend_usd = accumulated_cost_usd
        review.api_calls_used = accumulated_calls
        db.commit()
        os.unlink(sliced_path)
        if raise_on_error:
            raise RuntimeError(last_traceback)
        return review

    if result.status == "error":
        # Only a deliberate, non-retryable error (error_type="cap_exceeded")
        # can reach here — an "unexpected" one either succeeded on a later
        # attempt or was already handled by the result-is-None branch above.
        # Fix Round (2026-10-05), "per-classified-set spend cap": this IS
        # that same deliberate cap trip, just reported through review_
        # person_document's own structured return instead of a raised
        # exception — same "cancelled_spend_cap" status as the other path
        # above, never "failed" (a bug/crash status this explicitly is not).
        review.status = "cancelled_spend_cap"
        review.error_message = result.error
        # accumulated_cost_usd/accumulated_calls already include every
        # attempt's own extraction cost (including any earlier attempt
        # that raised before reaching review_person_document at all) —
        # this must still count toward the batch spend cap, same as the
        # success path below.
        review.api_calls_used = accumulated_calls + result.usage.api_calls
        review.spend_usd = accumulated_cost_usd + result.usage.estimated_cost_usd
        db.commit()
        os.unlink(sliced_path)
        return review

    os.unlink(sliced_path)

    # REAL BUG FOUND AND FIXED (urgent production crash — a real traceback:
    # "ValueError: not enough values to unpack (expected 3, got 2)" at the
    # zip-unpack below): everything from here through the final commit
    # used to run with NO try/except at all. Inside a FastAPI BackgroundTasks
    # callback (the automatic per-batch path, run_batch_reviews ->
    # run_review), an uncaught exception here has nowhere to surface — it
    # silently dies in the background thread, and the review is left
    # stuck at status="processing" forever, with no error visible from the
    # UI or any query. That's indistinguishable from a genuine hang, which
    # is exactly how this looked before the real traceback was found.
    # Wrapped the same way the earlier retry-loop stages already are:
    # mark "failed" with the FULL traceback, roll back any partial
    # rule_results this attempt may have already `db.add()`-ed (never a
    # half-written set — same all-or-nothing discipline the reference
    # project's own upload_pipeline.py documents for this exact shape of
    # work), and still re-raise for the manual endpoint's raise_on_error
    # contract.
    humanize_cost_usd = 0.0
    humanize_calls = 0
    try:
        # The post-review humanization pass — runs ONCE, after every rule
        # has already been evaluated and has its own real, raw reasoning
        # text, as a real Haiku call per finding (batched/parallel — see
        # agent-making's humanize.py::humanize_findings_batch). Replaces
        # the old client-side string-truncation heuristic (frontend/src/
        # lib/findings.ts), which structurally couldn't tell "safe to cut"
        # from "the conclusion is right here" — real bug found via a real
        # manual audit: it cut right at the comma before "but" in "X is
        # true, but Y contradicts it" (the shape nearly every FAIL reason
        # takes), silently dropping the entire reason a rule failed.
        # model_finding_raw keeps the real pre-humanize text as a separate,
        # permanent column (for the CSV export's own "raw vs humanized"
        # columns) — model_finding/final_finding now hold the humanized
        # text, same "write once" discipline as before.
        rule_ids = list(result.findings.keys())
        raw_texts = [_finding_text(result.findings[rid].get("evidence")) for rid in rule_ids]
        # Remaining headroom under the per-document hard cap AFTER
        # extraction + review_person_document's own real spend above — the
        # humanize pass gets only whatever's left, never the full cap (see
        # this function's own docstring on the hard spend cap).
        humanize_remaining_budget = max(
            0.0,
            settings.per_document_hard_cap_usd - accumulated_cost_usd - result.usage.estimated_cost_usd,
        )
        humanize_results = humanize_findings_batch(
            [t or "" for t in raw_texts], labels=rule_ids, max_spend_usd=humanize_remaining_budget,
        )
        humanized_by_rule_id: dict[str, tuple[str | None, str | None]] = {}
        for rid, raw_text, (_, humanized_text, usage) in zip(rule_ids, raw_texts, humanize_results):
            humanized_by_rule_id[rid] = (raw_text, humanized_text if raw_text is not None else None)
            if usage.get("input_tokens") or usage.get("output_tokens"):
                humanize_calls += 1
                humanize_cost_usd += usage.get("cost_usd", 0.0)

        rule_results: list[RuleResult] = []
        for rule_id, finding in result.findings.items():
            raw_text, humanized_text = humanized_by_rule_id[rule_id]
            rr = RuleResult(
                review_id=review.id,
                rule_id=rule_id,
                check_type=finding.get("check_type"),
                model_status=finding.get("result"),
                model_finding=humanized_text,
                model_finding_raw=raw_text,
                model_evidence=finding.get("evidence"),
                model_page=finding.get("page"),
                model_confidence=finding.get("confidence"),
                final_status=finding.get("result"),
                final_finding=humanized_text,
                final_pages=finding.get("page"),
            )
            db.add(rr)
            rule_results.append(rr)
        db.flush()

        review.score, review.audit_result = compute_score_and_audit_result(rule_results)
        # accumulated_calls/accumulated_cost_usd already include every
        # attempt's own extraction cost, including a retried-after-failure
        # attempt — same reasoning as the status="error" branch above.
        # Third real call site this document's review now makes: the
        # post-review humanize pass above (one Haiku call per finding) —
        # folded in here the same way session_note_extraction's own cost
        # was, so the batch spend cap actually sees every real dollar a
        # review spends, not just two of its three real call sites.
        review.api_calls_used = accumulated_calls + result.usage.api_calls + humanize_calls
        # All three real calls this document's review makes, each priced
        # at its own real rate (session_note_extraction's own CallTracker
        # now estimates cost per-provider — see agent_client.py/
        # model_provider.CallTracker — closing what was previously a
        # batch-spend-cap blind spot).
        review.spend_usd = accumulated_cost_usd + result.usage.estimated_cost_usd + humanize_cost_usd
        review.status = "complete"
        review.served_from_cache = False

        # Writes (or replaces, in place) the live cached answer for this
        # exact (cache_key, pipeline_version) — becomes the result a FUTURE
        # automatic lookup of this same document/pipeline reuses, including
        # when THIS run was itself a force_rerun. See review_cache.upsert's
        # own docstring for why a force_rerun still updates the cache.
        review_cache_module.upsert(
            db, cache_key=cache_key, pipeline_version=pipeline_version,
            payload={
                "review_extraction": review.extraction,
                "findings": {
                    rr.rule_id: {
                        "check_type": rr.check_type,
                        "model_status": rr.model_status,
                        "model_finding": rr.model_finding,
                        "model_finding_raw": rr.model_finding_raw,
                        "model_evidence": rr.model_evidence,
                        "model_page": rr.model_page,
                        "model_confidence": rr.model_confidence,
                    }
                    for rr in rule_results
                },
            },
        )

        audit_record(
            db, entity_type="session_note_review", entity_id=review.id,
            changes={
                "status": ("processing", "complete"),
                "audit_result": (None, review.audit_result),
                "score": (None, review.score),
                "person_document_id": (None, person_document.id),
            },
            actor=actor,
        )
        db.commit()
        db.refresh(review)
        return review
    except Exception:  # noqa: BLE001 — see this block's own comment above; never a silent background-task death
        db.rollback()  # undo any partial rule_results this attempt already db.add()-ed — never a half-written set
        review.status = "failed"
        review.error_message = traceback.format_exc()
        review.api_calls_used = accumulated_calls + result.usage.api_calls + humanize_calls
        review.spend_usd = accumulated_cost_usd + result.usage.estimated_cost_usd + humanize_cost_usd
        logger.error(
            "post-review processing (humanize pass / rule_results) failed for person_document_id=%s: %s",
            person_document.id, review.error_message,
        )
        db.commit()
        if raise_on_error:
            raise
        return review


@router.post(
    "/person-documents/{person_document_id}/review",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
)
def review_document(person_document_id: str, body: ReviewRequestIn, db: Session = Depends(get_db)) -> ReviewOut:
    """Manual (re-)run — creates a FRESH review row and runs it
    synchronously. Distinct from the automatic per-batch background job:
    every confident PersonDocument already gets a pending review row (and
    a queued background run) the moment its batch finishes classifying —
    see POST /batches — so this endpoint exists for an explicit re-review
    (e.g. after correcting a misclassification), not the normal path.
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

    review = SessionNoteReview(person_document_id=person_document.id, status="pending")
    db.add(review)
    db.flush()

    review = run_review(
        db, review, person_document, batch,
        actor=body.actor, model_override=body.model_override, force_rerun=body.force_rerun,
    )

    if review.status == "failed":
        # An exception-raising failure already propagated as a bare 500
        # before reaching here (raise_on_error=True, the default for this
        # manual path) — this covers the OTHER kind of failure,
        # review_person_document returning an expected result.status ==
        # "error" rather than raising, same 502 contract this endpoint has
        # always had.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"review failed: {review.error_message}")

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
    active: bool


def _person_document_out(person_document: PersonDocument, person: Person | None) -> PersonDocumentOut:
    return PersonDocumentOut(
        id=person_document.id, batch_id=person_document.batch_id, person_id=person_document.person_id,
        client_name=person.full_name if person else None,
        service_code=person_document.service_code, date_of_service=person_document.date_of_service,
        appendix=person_document.appendix, classification_confidence=person_document.classification_confidence,
        active=person_document.active,
    )


@router.get("/person-documents/{person_document_id}", response_model=PersonDocumentOut)
def get_person_document(person_document_id: str, db: Session = Depends(get_db)) -> PersonDocumentOut:
    """Minimal context for a person_document that may not have a review
    yet — the audit detail page's own "not reviewed yet" empty state uses
    this instead of erroring on GET .../review's 404."""
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    person = db.get(Person, person_document.person_id) if person_document.person_id else None
    return _person_document_out(person_document, person)


class ArchiveActionIn(BaseModel):
    actor: str


@router.patch("/person-documents/{person_document_id}/archive", response_model=PersonDocumentOut)
def archive_person_document(person_document_id: str, body: ArchiveActionIn, db: Session = Depends(get_db)) -> PersonDocumentOut:
    """Soft — sets active=false, hides it from the default Audits list,
    reversible via /unarchive. The recommended way to clean up a known-bad
    test audit; never removes the underlying rows (see CLAUDE.md's own
    no-hard-deletes invariant) — DELETE below is the deliberate, narrow
    exception to that, not this."""
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    if not person_document.active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already archived")

    person_document.active = False
    audit_record(
        db, entity_type="person_document", entity_id=person_document.id,
        changes={"active": (True, False)}, actor=body.actor,
    )
    db.commit()
    db.refresh(person_document)
    person = db.get(Person, person_document.person_id) if person_document.person_id else None
    return _person_document_out(person_document, person)


@router.patch("/person-documents/{person_document_id}/unarchive", response_model=PersonDocumentOut)
def unarchive_person_document(person_document_id: str, body: ArchiveActionIn, db: Session = Depends(get_db)) -> PersonDocumentOut:
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    if person_document.active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not archived")

    person_document.active = True
    audit_record(
        db, entity_type="person_document", entity_id=person_document.id,
        changes={"active": (False, True)}, actor=body.actor,
    )
    db.commit()
    db.refresh(person_document)
    person = db.get(Person, person_document.person_id) if person_document.person_id else None
    return _person_document_out(person_document, person)


class DeleteForeverIn(BaseModel):
    actor: str
    reason: str


@router.post("/person-documents/{person_document_id}/delete-forever", status_code=status.HTTP_204_NO_CONTENT)
def delete_person_document_forever(person_document_id: str, body: DeleteForeverIn, db: Session = Depends(get_db)) -> None:
    """A DELIBERATE, EXPLICIT exception to this project's usual no-hard-
    deletes convention (CLAUDE.md) — Krishna specifically asked for a way
    to permanently remove known-bad early test audits (pre-goal-counting-
    fix extractions), not a general pattern to reuse elsewhere without
    being asked again. Real, permanent, cascading: every SessionNoteReview
    for this document and every RuleResult under those reviews is deleted
    along with the PersonDocument itself.

    The audit_log entry documenting WHO deleted WHAT and WHY is written
    and committed FIRST, in its own transaction, specifically so it
    survives even though the row it describes will not — the one place in
    this codebase an audit trail entry is deliberately NOT in the same
    transaction as the change it describes, because "in the same
    transaction as a hard delete" would mean it could vanish too.
    """
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")

    review_ids = list(db.execute(
        select(SessionNoteReview.id).where(SessionNoteReview.person_document_id == person_document_id)
    ).scalars())  # .scalars() already yields the plain id strings — no .id attribute access needed

    audit_record(
        db, entity_type="person_document", entity_id=person_document.id,
        changes={
            "deleted_forever": (False, True),
            "reason": (None, body.reason),
            "review_ids_deleted": (None, review_ids),
        },
        actor=body.actor,
    )
    db.commit()  # the audit entry survives independent of what happens below

    for review_id in review_ids:
        db.execute(RuleResult.__table__.delete().where(RuleResult.review_id == review_id))
    db.execute(SessionNoteReview.__table__.delete().where(SessionNoteReview.person_document_id == person_document_id))
    db.delete(person_document)
    db.commit()


@router.get("/person-documents/{person_document_id}/pdf")
def get_person_document_pdf(person_document_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """This document's own page range only (page_start–page_end), sliced
    out of the batch's original PDF — never the whole batch file — for the
    audit detail page's left-hand document viewer.

    BUG FIX: Starlette's FileResponse defaults content_disposition_type to
    "attachment" the moment a `filename` is passed — that alone was
    forcing a download prompt in the browser regardless of anything the
    frontend did with the response. `content_disposition_type="inline"`
    is the actual fix; the frontend's PDF viewer (react-pdf) fetches this
    as raw bytes anyway, but a correct inline disposition is still what
    makes this URL behave sanely if ever opened directly.
    """
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    batch = db.get(SessionNoteBatch, person_document.batch_id)
    sliced_path = slice_pdf_to_temp_file(batch.original_pdf_path, person_document.page_start, person_document.page_end)
    return FileResponse(
        sliced_path, media_type="application/pdf", filename=f"{person_document_id}.pdf",
        content_disposition_type="inline",
    )


@router.get("/person-documents/{person_document_id}/activity-statement-pdf")
def get_activity_statement_pdf(person_document_id: str, db: Session = Depends(get_db)) -> FileResponse:
    """This person's own single "Activity Statement - <name>" cover page —
    the real timesheet document the SN-*-04/SN-*-09 checks now resolve
    against (see PersonDocument.activity_statement_page's own docstring) —
    sliced out of the SAME batch PDF, so a non-technical reviewer can
    independently confirm a flagged mismatch against the real source.
    404 when this batch predates the column, or this row is 'unresolved'
    (no person, so no cover page was ever attributed) — never guessed at.
    """
    person_document = db.get(PersonDocument, person_document_id)
    if person_document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person_document not found")
    if person_document.activity_statement_page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no activity statement recorded for this document")
    batch = db.get(SessionNoteBatch, person_document.batch_id)
    page = person_document.activity_statement_page
    sliced_path = slice_pdf_to_temp_file(batch.original_pdf_path, page, page)
    return FileResponse(
        sliced_path, media_type="application/pdf", filename=f"{person_document_id}-activity-statement.pdf",
        content_disposition_type="inline",
    )


class ExtractionOut(BaseModel):
    status: str
    full_text: str | None
    fields: dict | None  # session_note_extraction.py's 6-field structured output
    data_points: list[dict] | None  # [{provider, goal, date}, ...] the goal-counting/history checks used


@router.get("/person-documents/{person_document_id}/extraction", response_model=ExtractionOut)
def get_extraction(person_document_id: str, db: Session = Depends(get_db)) -> ExtractionOut:
    """The full, unfiltered "View Full Extraction" page's own data source —
    every data point actually pulled from the document and used in
    matching/checking, not just the summary fields on the main detail
    page. Reads the LATEST review for this document, whatever its status."""
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
    extraction = review.extraction or {}
    return ExtractionOut(
        status=review.status,
        full_text=review.full_text,
        fields=extraction.get("fields"),
        data_points=extraction.get("data_points"),
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
