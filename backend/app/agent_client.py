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
from agent.pipeline.api import classify_batch_pdf as _classify_batch_pdf  # noqa: E402
from agent.pipeline.api import review_person_document as _review_person_document  # noqa: E402
from agent.pipeline.extract import extract_pdf_text as _extract_pdf_text  # noqa: E402

_RULES_JSON_PATH = _AGENT_MAKING_PATH / "agent" / "rules" / "rules.json"


def classify_batch_pdf(pdf_path: str) -> dict:
    """See agent-making/agent/pipeline/classify_batch.py for the full
    BatchClassificationResult shape. Zero model calls."""
    return _classify_batch_pdf(pdf_path)


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
        "bcba_name": _fields_module.bcba_lba_header_name(fields),
        "session_start_time": _fields_module.session_start_time(fields),
        "session_end_time": _fields_module.session_end_time(fields),
    }


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
):
    """Forwards this backend's own configured caps
    (rule_engine_max_calls/rule_engine_max_spend_usd) — a backend process
    calling this on every review should never be able to runaway-retry
    into an unbounded bill, independent of whatever default agent-making's
    own ApiCallTracker would otherwise apply.
    """
    return _review_person_document(
        pdf_path,
        service_code,
        prior_extractions=prior_extractions,
        max_calls=settings.rule_engine_max_calls,
        max_spend_usd=settings.rule_engine_max_spend_usd,
        model_override=model_override,
    )
