"""Public wrapper — the only entry point other code (a future backend, the
Streamlit app) should import. Extends Phase 1's classify_batch_pdf with the
actual rule-checking pipeline: `review_person_document`.

Orchestration order: extract -> deterministic checks (fields.py) -> the
judgment layer (judge.py's majority vote, via integrity.py's retry/page-
recovery wrapper) for every rule not resolved deterministically, with
history_comparison.py's extra_context wired in for the needs_history subset
-> merge.py combines both layers -> humanize.py cleans every evidence
string -> one ReviewResult, JSON-serializable, returned to the caller.

Every known failure mode (a corrupt PDF, an exhausted API-call cap, an
IntegrityError) is caught here and returned as a structured `error` field —
never a raw exception reaching the caller.
"""
from __future__ import annotations

import hashlib
import json
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fields as fields_module
from . import history_comparison as history_comparison_module
from . import humanize as humanize_module
from . import integrity as integrity_module
from . import judge as judge_module
from . import merge as merge_module
from .call_tracker import ApiCallCapExceeded, ApiCallTracker, ApiSpendCapExceeded
from .classify_batch import classify_batch
from .extract import extract_pdf_text
from .humanize import humanize_evidence
from .integrity import IntegrityError

SCHEMA_VERSION = "1.0"

RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "rules.json"


def _load_rules() -> list[dict]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]


def classify_batch_pdf(pdf_path: str) -> dict:
    """Phase 1's public wrapper — see classify_batch.py for the full shape.
    Zero model calls anywhere in this call chain."""
    pages = extract_pdf_text(pdf_path)
    return classify_batch(pages)


# Every pipeline module whose code can change a finding's result/evidence
# text for the SAME input `fields` dict — i.e. everything downstream of
# extraction that pipeline_version_fingerprint() must cover so a code
# change here can never be served from a stale cached verdict. Listed
# explicitly (not "every .py file in this package") so an unrelated change
# elsewhere (e.g. classify_batch.py, extract.py — covered by the backend's
# own full_text-based cache key instead, since their output IS full_text)
# doesn't force every cache entry to invalidate for no real reason.
_FINGERPRINTED_MODULE_PATHS = [
    Path(fields_module.__file__),
    Path(judge_module.__file__),
    Path(integrity_module.__file__),
    Path(merge_module.__file__),
    Path(humanize_module.__file__),
    Path(history_comparison_module.__file__),
]


def pipeline_version_fingerprint() -> str:
    """Fix Round (2026-10-05), "non-determinism fix": the backend's own
    review-result cache (see backend/app/services/review_cache.py) must
    never serve a verdict computed under an OLD ruleset or OLD rule-
    checking/prompt code — this is the one function it calls to find out.
    Returns a sha256 hex digest that changes automatically the moment
    EITHER of the following changes, with zero manual version-bumping
    required anywhere, ever:

    - rules.json's raw file bytes (any rule's wording/params/active flag/
      severity/anything else).
    - the raw source bytes of every pipeline module in
      _FINGERPRINTED_MODULE_PATHS above — covers a deterministic-checker
      wording fix (fields.py), a judgment-prompt change (judge.py), a
      page-recovery-note reword (integrity.py), a reconciliation-logic
      change (merge.py), a humanize-prompt change (humanize.py), and a
      history-comparison-logic change (history_comparison.py). Hashing
      whole files, not hand-picked constants/functions, deliberately: the
      failure mode this guards against is a future code change nobody
      remembers to also reflect in the fingerprint, and a whole-file hash
      cannot be forgotten to update the way a hand-picked list can.

    Deliberately NOT included: `judge.py`'s model name/call parameters
    (model_override is a caller-supplied runtime choice, not a pipeline
    version) — a run under a different model is a genuinely different
    computation the caller asked for, not a "stale cache" situation, and
    is out of scope for this round's fix (see this round's own report).
    """
    h = hashlib.sha256()
    h.update(RULES_PATH.read_bytes())
    for module_path in _FINGERPRINTED_MODULE_PATHS:
        h.update(module_path.read_bytes())
    return h.hexdigest()


@dataclass
class UsageInfo:
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class ReviewResult:
    schema_version: str = SCHEMA_VERSION
    status: str = "complete"  # "complete" | "error"
    service_code: str | None = None
    findings: dict = field(default_factory=dict)
    export_rows: list = field(default_factory=list)
    needs_review: list = field(default_factory=list)
    usage: UsageInfo = field(default_factory=UsageInfo)
    error: str | None = None
    # "cap_exceeded" (ApiCallCapExceeded/ApiSpendCapExceeded/IntegrityError —
    # a deliberate limit; retrying changes nothing) vs "unexpected" (the
    # blanket exception handler below — anything else, including a real,
    # once-off SDK/library crash). Real bug found via a real production
    # crash (PydanticUserError inside the real Anthropic call): before this
    # field existed, BOTH buckets looked identical to a caller as
    # status="error" with no way to tell "retrying this is pointless" apart
    # from "this might just be transient" — the backend's own retry
    # mechanism (app/routers/person_documents.py::run_review) reads this to
    # decide whether to retry at all. None only for status="complete".
    error_type: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _humanize_findings(findings: dict) -> dict:
    """Applies the free, deterministic tone/artifact cleanup pass to every
    finding's evidence — never the real-LLM rewrite (humanize_evidence_
    with_llm), which is a real, billed Anthropic call and stays opt-in only,
    per CLAUDE.md's hard rule."""
    cleaned = {}
    for rule_id, entry in findings.items():
        evidence = entry.get("evidence")
        if isinstance(evidence, str):
            entry = {**entry, "evidence": humanize_evidence(evidence)}
        elif isinstance(evidence, list):
            entry = {**entry, "evidence": [
                {**item, "detail": humanize_evidence(item["detail"])} if isinstance(item, dict) and isinstance(item.get("detail"), str) else item
                for item in evidence
            ]}
        cleaned[rule_id] = entry
    return cleaned


def review_person_document(
    pdf_path: str,
    service_code: str,
    prior_extractions: list[dict] | None = None,
    max_calls: int | None = None,
    max_spend_usd: float | None = None,
    model_override: str | None = None,
    timesheet_rows: list[dict] | None = None,
    appendix: str | None = None,
    sibling_documents: list[dict] | None = None,
) -> ReviewResult:
    """Reviews ONE person's ONE document (a single appendix/service-code
    document, per Phase 1's classify_person_docs.py — not a whole multi-
    appendix batch cover-page range) against every active rules.json rule
    whose service_code matches.

    `prior_extractions`: this person's prior session documents, as plain
    data (`[{"date_of_service": str, "full_text": str}, ...]`) — agent-
    making stays stateless; the caller (a future backend) is responsible
    for fetching these, never this function. `None`/empty means no history
    is available; every needs_history rule then resolves to not_checkable
    without ever reaching the judgment layer for that rule_id.

    `timesheet_rows`/`appendix`: this document's OWN Activity Statement
    cover page, already parsed into rows by activity_statement.py, plus
    which row is this document's own (by appendix — a reliable join key,
    not date/service_code guessing). Same statelessness convention as
    prior_extractions: the caller (the backend) is responsible for slicing
    the right cover page and parsing it — this function only consumes
    already-parsed data. `None` means no Activity Statement data was
    available for this batch; every timesheet-dependent rule then
    resolves to an honest not_checkable, same as before this was wired up.

    `sibling_documents`: this person's OTHER documents from the SAME
    batch/upload (any service code), as plain data
    (`[{"service_code": str, "full_text": str}, ...]`) — same
    statelessness convention as prior_extractions/timesheet_rows: the
    caller fetches and slices these, this function only consumes
    already-extracted text. For any rule phrased as "at least one session
    note" rather than scoped to a specific assessment/service type (see
    fields.py::_check_SN_97151_11). `None`/empty means no sibling data was
    available; that rule then resolves to not_checkable, same as before
    this was wired up.

    `max_calls`/`max_spend_usd`: forwarded to this call's own
    ApiCallTracker — the only thing standing between "run this" and an
    unbounded number of real, billed API calls, or an unbounded real
    dollar spend. `max_spend_usd=None` uses the tracker's own env-configured
    default (`MAX_REAL_SPEND_USD`, $4.00 if unset) — see call_tracker.py.
    `model_override=None` (every real production caller) means the real,
    billed Anthropic path; anything else routes through model_provider.py's
    free/cheap OpenRouter-default path — see that module's own docstring.
    """
    tracker = ApiCallTracker(max_calls=max_calls, max_spend_usd=max_spend_usd)
    try:
        pages = extract_pdf_text(pdf_path)
        full_text = "\n\n".join(p["text"] for p in pages)
        rules = [r for r in _load_rules() if r.get("service_code") == service_code]

        fields = {
            "pages": pages, "full_text": full_text, "service_code": service_code,
            "timesheet_rows": timesheet_rows, "appendix": appendix,
            "sibling_documents": sibling_documents,
        }

        det_results, escalated_rules = fields_module.run_deterministic_checks(rules, fields)

        judgment_results: dict[str, dict] = {
            r["rule_id"]: {
                "result": "not_checkable",
                "evidence": r.get("notes") or "Not checkable by design — see rules.json.",
                "page": None,
                "confidence": 0.0,
            }
            for r in rules
            if r["active"] and r["check_type"] == "not_checkable"
        }

        judgment_rules = [
            r for r in rules
            if r["active"] and (r["check_type"] == "judgment" or r in escalated_rules)
        ]

        needs_history_ids = [r["rule_id"] for r in judgment_rules if r.get("needs_history")]
        history_context = history_comparison_module.build_history_context(
            needs_history_ids, full_text, prior_extractions,
        )

        no_history_ids = [rid for rid, ctx in history_context.items() if ctx is None]
        for rule_id in no_history_ids:
            judgment_results[rule_id] = history_comparison_module.NOT_CHECKABLE_NO_HISTORY

        rules_for_judgment = [
            {**r, "extra_context": history_context.get(r["rule_id"])}
            for r in judgment_rules
            if r["rule_id"] not in no_history_ids
        ]

        stabilized_ids = {r["rule_id"] for r in rules_for_judgment if r["rule_id"] in judge_module.STABILIZED_UNCERTAIN_RULE_IDS}
        rules_for_judgment = [r for r in rules_for_judgment if r["rule_id"] not in stabilized_ids]
        for rule_id in stabilized_ids:
            judgment_results[rule_id] = judge_module.stabilized_uncertain_finding(rule_id)

        if rules_for_judgment:
            judgment_results.update(
                integrity_module.run_judgment_with_integrity_check(
                    rules_for_judgment, fields, rendered_images={}, tracker=tracker, model_override=model_override,
                )
            )

        merged = merge_module.merge_findings(rules, det_results, judgment_results)
        merged["findings"] = _humanize_findings(merged["findings"])

        return ReviewResult(
            status="complete",
            service_code=service_code,
            findings=merged["findings"],
            export_rows=merged["export_rows"],
            needs_review=merged["needs_review"],
            usage=UsageInfo(
                api_calls=tracker.count,
                input_tokens=tracker.total_input_tokens,
                output_tokens=tracker.total_output_tokens,
                estimated_cost_usd=tracker.estimated_cost(),
            ),
        )
    except (ApiCallCapExceeded, ApiSpendCapExceeded, IntegrityError) as exc:
        return ReviewResult(status="error", service_code=service_code, error=str(exc), error_type="cap_exceeded")
    except Exception:  # noqa: BLE001 — never let a raw exception reach the caller
        # The FULL traceback, not just str(exc) — the previous, message-
        # only form made a real production crash (PydanticUserError:
        # "BaseModel cannot be instantiated directly") undiagnosable after
        # the fact, with no file/line pointing at the actual call site.
        return ReviewResult(status="error", service_code=service_code, error=traceback.format_exc(), error_type="unexpected")
