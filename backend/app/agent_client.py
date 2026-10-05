"""The ONLY file in this backend allowed to import from `agent-making`.
Every other backend module that needs classification/review results goes
through the functions defined here, never through `agent.pipeline.*`
directly — a single, stable boundary, same reasoning as the old TP-review
project's own app/agent_client.py.

Real Anthropic calls only ever happen inside `review_person_document`
below — the same call site agent-making itself gates behind CLAUDE.md's
hard rule and its own pytest autouse guardrail (agent-making/agent/tests/
conftest.py). This module adds no second path to a real key anywhere; it
just calls the one function agent-making already exposes, with this
backend's own configured caps.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from app.config import settings

# agent-making isn't an installed package — make its `agent` package
# importable by path. Resolved once, at import time. Inserts agent-making/
# itself (not agent-making/agent/) onto sys.path, since agent-making's own
# internal code imports itself as `agent.pipeline...` (confirmed against
# agent-making/agent/app.py and agent-making/agent/tests/conftest.py, both
# of which insert the SAME parent directory) — not the bare `pipeline...`
# the prior TP-review project's own agent-making used.
_AGENT_MAKING_PATH = Path(settings.agent_making_path)
if not _AGENT_MAKING_PATH.is_absolute():
    _AGENT_MAKING_PATH = (Path(__file__).resolve().parents[1] / _AGENT_MAKING_PATH).resolve()
if str(_AGENT_MAKING_PATH) not in sys.path:
    sys.path.insert(0, str(_AGENT_MAKING_PATH))

if settings.anthropic_api_key:
    # setdefault, not direct assignment — agent-making's own .env (loaded
    # the moment model_provider.py is imported below) wins if it already
    # set this; this is only a fallback for a deploy that doesn't ship
    # agent-making/.env at all.
    os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
if settings.openrouter_api_key:
    os.environ.setdefault("OPENROUTER_API_KEY", settings.openrouter_api_key)

import json

from agent.pipeline import fields as _fields_module  # noqa: E402
from agent.pipeline.activity_statement import (  # noqa: E402
    parse_activity_statement_rows as _parse_activity_statement_rows,
)
from agent.pipeline.api import classify_batch_pdf as _classify_batch_pdf  # noqa: E402
from agent.pipeline.api import pipeline_version_fingerprint as _pipeline_version_fingerprint  # noqa: E402
from agent.pipeline.api import review_person_document as _review_person_document  # noqa: E402
from agent.pipeline.call_tracker import ApiCallCapExceeded  # noqa: E402
from agent.pipeline.call_tracker import ApiSpendCapExceeded  # noqa: E402
from agent.pipeline.extract import extract_pdf_text as _extract_pdf_text  # noqa: E402
from agent.pipeline.humanize import humanize_findings_batch as _humanize_findings_batch  # noqa: E402
from agent.pipeline.model_provider import CallTracker as _CallTracker  # noqa: E402
from agent.pipeline.model_provider import ModelCallCapExceeded  # noqa: E402
from agent.pipeline.session_note_extraction import (  # noqa: E402
    extract_session_note_file as _extract_session_note_file,
)

_RULES_JSON_PATH = _AGENT_MAKING_PATH / "agent" / "rules" / "rules.json"

# Fix Round (2026-10-05), "per-classified-set spend cap": every real spend-
# cap exception this backend's three real call stages can raise — from
# BOTH of agent-making's two tracker implementations (call_tracker.
# ApiCallTracker, used by review_person_document's own judgment layer; and
# model_provider.CallTracker, used by extract_session_note_data and
# humanize_findings_batch). A deliberate, expected safety-ceiling trip,
# never a real provider/network failure — see run_review's own use of
# this tuple for why it's caught separately from "unexpected" exceptions
# and never retried.
SPEND_CAP_EXCEEDED_EXCEPTIONS = (ApiCallCapExceeded, ApiSpendCapExceeded, ModelCallCapExceeded)


def classify_batch_pdf(pdf_path: str) -> dict:
    """See agent-making/agent/pipeline/classify_batch.py for the full
    BatchClassificationResult shape. Zero model calls."""
    return _classify_batch_pdf(pdf_path)


def pipeline_version_fingerprint() -> str:
    """See agent-making/agent/pipeline/api.py's own docstring — a sha256
    hex digest over rules.json plus every pipeline module that can change
    a finding's result/evidence text. app/services/review_cache.py uses
    this as half of its cache key (the other half is the document content
    hash) so a stale cached verdict can never be served after a rules/
    prompt change. Zero model calls."""
    return _pipeline_version_fingerprint()


def extract_pdf_full_text(pdf_path: str) -> str:
    """Plain, zero-cost page-text extraction (no model call) — used to
    build this review's own `full_text` for a LATER document's
    prior_extractions, and for nothing else. Not session_note_extraction.py's
    6-field structured extraction (that's a real, billed-by-default-unless-
    OpenRouter model call, deliberately NOT wired into the review endpoint
    in this pass — seeCLAUDE.md's hard rule and this phase's own report)."""
    pages = _extract_pdf_text(pdf_path)
    return "\n\n".join(p["text"] for p in pages)


def extract_document_summary_fields(pdf_path: str) -> dict:
    """Plain, zero-cost structural extractions (agent-making's own
    fields.py helpers, no model call) for the Audit Results list's
    Provider/BCBA/Start Time/End Time columns. Returns
    {provider_name, bcba_name, session_start_time, session_end_time},
    each None if the source document genuinely doesn't print it."""
    pages = _extract_pdf_text(pdf_path)
    fields = {"pages": pages, "full_text": "\n\n".join(p["text"] for p in pages)}
    return {
        "provider_name": _fields_module.provider_name(fields),
        # bcba_display_name(), NOT bcba_lba_header_name() directly — falls
        # back to the provider's own name when the note's template has no
        # separate 'BCBA/LBA:' field AND the provider's own credentials
        # include BCBA (97151/97156 notes: the provider IS the BCBA, one
        # person, not two). SN-97153-05's own judgment check is unaffected
        # — it calls bcba_lba_header_name() directly, unchanged. See
        # fields.py::bcba_display_name's own docstring for the real-data
        # evidence behind this.
        "bcba_name": _fields_module.bcba_display_name(fields),
        "session_start_time": _fields_module.session_start_time(fields),
        "session_end_time": _fields_module.session_end_time(fields),
    }


def extract_data_points(pdf_path: str) -> list[dict]:
    """The [(provider, goal_text, date), ...] 'X added a data point to
    <goal> for MM/DD/YYYY' bullets fields.py's own goal-counting
    (SN-97153-03) and history-comparison checks already parse out of the
    note, zero-cost (regex, no model call) — surfaced here so the
    frontend's "View Full Extraction" page can show the exact data points
    those checks actually used, not just their pass/fail verdict."""
    pages = _extract_pdf_text(pdf_path)
    fields = {"pages": pages, "full_text": "\n\n".join(p["text"] for p in pages)}
    return [
        {"provider": provider, "goal": goal, "date": d.isoformat()}
        for provider, goal, d in _fields_module.data_point_bullets(fields)
    ]


def extract_session_note_data(
    pdf_path: str, *, model_override: str | None = None, max_spend_usd: float | None = None,
) -> dict:
    """session_note_extraction.py's 6-field structured extraction
    (session_date/session_location/clinician_telehealth_location/
    patient_telehealth_location/assessment_activity/note_detail_level) —
    a REAL, separately-billed model call (OpenRouter by default, an
    Anthropic fallback only if that's exhausted), distinct from and IN
    ADDITION TO review_person_document's own call(s) below. `api_cost_usd`
    is CallTracker's own real, per-provider token-based estimate (Haiku
    fallback priced at Haiku's own rate, never Sonnet's) — previously a
    known gap where this call's cost was invisible to the batch spend cap;
    see app/routers/person_documents.py::run_review, which now folds this
    into SessionNoteReview.spend_usd. Cached by agent-making itself
    (content-hash keyed), so re-processing the identical file never
    repeats the call (and costs nothing on a cache hit).

    `max_spend_usd`: the caller's own REMAINING headroom under the
    per-document hard cap (settings.per_document_hard_cap_usd) — see
    run_review's own docstring for why each real call stage gets only
    its own remaining share, never the full cap, so the three stages
    combined can never exceed it.
    """
    tracker = _CallTracker(max_calls=settings.session_note_extraction_max_calls, max_spend_usd=max_spend_usd)
    result = _extract_session_note_file(pdf_path, tracker=tracker, model_override=model_override)
    return {"fields": result, "api_calls_used": tracker.count, "api_cost_usd": tracker.estimated_cost_usd}


def extract_activity_statement_rows(pdf_path: str, page_number: int) -> list[dict]:
    """Zero-cost, regex-only parse of ONE page of the batch's own PDF — the
    "Activity Statement - <name>" cover page classify_batch_pdf already
    used as a page-boundary marker (page_number is PersonDocument.
    activity_statement_page, 1-indexed). No model call, no second upload —
    this is the same document already sitting in the batch's own
    original_pdf_path. See agent-making/agent/pipeline/activity_statement.py."""
    pages = _extract_pdf_text(pdf_path)
    page_text = pages[page_number - 1]["text"]
    return _parse_activity_statement_rows(page_text)


def load_rules() -> list[dict]:
    """rules.json is the single source of truth for rule metadata
    (type/severity/flag/description) — the backend looks this up at
    response-build time rather than duplicating it into the database,
    matching the standing 'thin wrapper, no rule logic in the backend'
    boundary."""
    return json.loads(_RULES_JSON_PATH.read_text(encoding="utf-8"))["rules"]


def review_person_document(
    pdf_path: str,
    service_code: str,
    *,
    prior_extractions: list[dict] | None = None,
    model_override: str | None = None,
    timesheet_rows: list[dict] | None = None,
    appendix: str | None = None,
    sibling_documents: list[dict] | None = None,
    max_spend_usd: float | None = None,
):
    """Forwards this backend's own configured caps
    (rule_engine_max_calls/rule_engine_max_spend_usd) — a backend process
    calling this on every review should never be able to runaway-retry
    into an unbounded bill, independent of whatever default agent-making's
    own ApiCallTracker would otherwise apply.

    timesheet_rows/appendix: this backend's own responsibility to fetch and
    slice (agent-making is stateless — see extract_activity_statement_rows
    above) — the caller passes the batch's own Activity Statement rows plus
    this PersonDocument's own appendix, so the timesheet-alignment
    deterministic checks (fields.py::_check_timesheet_alignment et al.) can
    resolve to a real pass/fail instead of not_checkable.

    sibling_documents: same convention — this person's OTHER documents
    from the SAME batch/upload (any service code), for any rule phrased
    as "at least one session note" (see fields.py::_check_SN_97151_11).

    `max_spend_usd`: the caller's own REMAINING headroom under the
    per-document hard cap (run_review's own docstring) — when given, the
    STRICTER of this and the flat rule_engine_max_spend_usd setting wins,
    since the per-document cap must never be loosened by this override,
    only tightened.
    """
    effective_max_spend_usd = settings.rule_engine_max_spend_usd
    if max_spend_usd is not None:
        effective_max_spend_usd = min(effective_max_spend_usd, max_spend_usd)
    return _review_person_document(
        pdf_path,
        service_code,
        prior_extractions=prior_extractions,
        max_calls=settings.rule_engine_max_calls,
        max_spend_usd=effective_max_spend_usd,
        model_override=model_override,
        timesheet_rows=timesheet_rows,
        appendix=appendix,
        sibling_documents=sibling_documents,
    )


def humanize_findings_batch(
    texts: list[str], *, labels: list[str] | None = None, max_spend_usd: float | None = None,
) -> list[tuple[str, str, dict]]:
    """Forwards this backend's own configured cap (humanize_max_calls) —
    same reasoning as review_person_document's own caps above. Runs AFTER
    a document's full review already has its real, raw reasoning text for
    every rule (see app/routers/person_documents.py::run_review, the one
    real caller) — a separate, real Haiku call per finding, replacing the
    old client-side string-truncation heuristic that structurally
    couldn't tell "safe to cut" from "the conclusion is right here" (see
    agent-making's humanize.py::humanize_findings_batch for the real bug
    this replaced). Returns one (raw_text, humanized_text, usage) tuple
    per input text, same order as `texts`.

    `max_spend_usd`: the caller's own REMAINING headroom under the
    per-document hard cap — see run_review's own docstring.
    """
    return _humanize_findings_batch(
        texts, max_calls=settings.humanize_max_calls, max_spend_usd=max_spend_usd, labels=labels,
    )
