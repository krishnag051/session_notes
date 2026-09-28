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

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fields as fields_module
from . import history_comparison as history_comparison_module
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

        fields = {"pages": pages, "full_text": full_text, "service_code": service_code}

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
        return ReviewResult(status="error", service_code=service_code, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — never let a raw exception reach the caller
        return ReviewResult(status="error", service_code=service_code, error=f"{type(exc).__name__}: {exc}")
