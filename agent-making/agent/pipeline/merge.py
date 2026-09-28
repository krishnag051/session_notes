"""Combines the deterministic and judgment layers into one findings object.
Lifted from the prior project's merge.py (same defensive coercion and
page-exploding logic — a finding's own shape is advisory from judge.py's
tool schema, not runtime-enforced, so this stays defense-in-depth here
too), adapted to this project's rules.json shape: no action_lane/
action_tag/category (not decided for this project — see CLAUDE.md's
invariants) — service_code/type/severity/flag instead.
"""
import json
import logging

from .integrity import PAGE_UNAVAILABLE_NOTE

logger = logging.getLogger(__name__)

NEEDS_ACTION_RESULTS = {"fail", "uncertain"}

# Page citation is a hard, required field on every pass/fail/uncertain
# finding, with one sanctioned exception -- genuinely nothing exists
# anywhere to cite. The judgment layer already discloses this honestly via
# integrity.py's own PAGE_UNAVAILABLE_NOTE. The deterministic layer has no
# equivalent recovery pass, so this is the one central place, after det+
# judgment converge, where a deterministic finding that still has no page
# gets the same honest disclosure rather than silently shipping a blank
# page field. not_applicable/not_checkable are excluded: their own
# evidence text already states plainly why there's nothing to check.
_PAGE_REQUIRED_RESULTS = {"pass", "fail", "uncertain"}


def _format_page_display(page) -> str | int | None:
    """A finding's `page` can be a single int, a list of 2+ ints (one
    finding whose evidence genuinely spans multiple specific pages
    together), or None. Renders the list case as a human-readable string:
    consecutive pages as "14-15", non-consecutive as "11, 14".

    A malformed page value (anything else — the schema is advisory to the
    model, not enforced on the response side) is treated as "not
    page-specific" (None) rather than crashing the whole review over one
    rule's malformed field.
    """
    if page is None or isinstance(page, int):
        return page
    if not isinstance(page, list) or not all(isinstance(p, int) for p in page):
        logger.warning(
            "merge._format_page_display: malformed page value %r (expected int, list[int], or "
            "None) -- treating as not-page-specific (None) rather than crashing.",
            page,
        )
        return None
    pages = sorted(page)
    if len(pages) == 1:
        return pages[0]
    is_consecutive = all(b - a == 1 for a, b in zip(pages, pages[1:]))
    if is_consecutive:
        return f"{pages[0]}-{pages[-1]}"
    return ", ".join(str(p) for p in pages)


def _coerce_detail_to_string(evidence) -> str:
    """Same coercion policy as judge.py's own helper (kept duplicated
    rather than imported — this module's job is "combine the deterministic
    and judgment layers," not reach back into either layer's internals)."""
    if isinstance(evidence, str):
        return evidence
    if isinstance(evidence, dict) and isinstance(evidence.get("detail"), str):
        return evidence["detail"]
    logger.warning(
        "merge._explode_to_rows: malformed evidence value %r (expected str) -- "
        "coercing to its JSON representation rather than crashing.",
        evidence,
    )
    return json.dumps(evidence)


def _explode_to_rows(rule_id: str, entry: dict) -> list[dict]:
    """One export row per page-level entry when `evidence` is the
    {page, detail} list form; a single row (unchanged) when it's a plain
    string."""
    base = {
        "rule_id": rule_id,
        "service_code": entry["service_code"],
        "type": entry["type"],
        "severity": entry["severity"],
        "flag": entry["flag"],
        "result": entry["result"],
        "confidence": entry["confidence"],
    }
    if isinstance(entry["evidence"], list):
        return [
            {**base, "page": item["page"], "detail": item["detail"]}
            for item in entry["evidence"]
        ]
    return [{**base, "page": _format_page_display(entry["page"]), "detail": _coerce_detail_to_string(entry["evidence"])}]


def merge_findings(rules: list[dict], det_results: dict[str, dict], judgment_results: dict[str, dict]) -> dict:
    """Returns:
    {
        "findings": {rule_id: {result, evidence, page, confidence,
                                service_code, type, severity, flag, check_type}},
        "export_rows": [{rule_id, service_code, type, severity, flag, result,
                          page, detail, confidence}, ...],  # one row per
                          page-level entry when evidence is multi-page, one
                          row per rule_id otherwise
        "needs_review": [rule_id, ...],  # active, result in {fail, uncertain}
    }
    """
    findings = {}
    export_rows = []
    needs_review = []

    for rule in rules:
        if not rule["active"]:
            continue
        rule_id = rule["rule_id"]
        layer_result = det_results.get(rule_id) if rule["check_type"] == "deterministic" else judgment_results.get(rule_id)
        if layer_result is None:
            continue

        # One rule's own resolution failing must land ONLY that rule on a
        # safe, clearly-marked fallback, never take the rest of the review
        # down with it (this loop runs once per rule in a single-document
        # review — no isolation here would mean one bad `page`/`evidence`
        # shape discarding every other rule's already-computed finding).
        try:
            entry = {
                **layer_result,
                "service_code": rule.get("service_code"),
                "type": rule.get("type"),
                "severity": rule.get("severity"),
                "flag": rule.get("flag"),
                "check_type": rule["check_type"],
            }
            if (
                rule["check_type"] == "deterministic"
                and entry.get("result") in _PAGE_REQUIRED_RESULTS
                and not entry.get("page")
                and isinstance(entry.get("evidence"), str)
                and PAGE_UNAVAILABLE_NOTE not in entry["evidence"]
            ):
                entry["evidence"] = entry["evidence"] + PAGE_UNAVAILABLE_NOTE
            rows = _explode_to_rows(rule_id, entry)
        except Exception:
            logger.exception(
                "merge_findings: building the export entry for %s failed -- falling back to an "
                "'uncertain, resolution failed' finding for THIS rule only; every other rule's "
                "real result in this same review is unaffected.",
                rule_id,
            )
            entry = {
                "result": "uncertain",
                "evidence": "This item could not be resolved. It's marked as needing human review rather than guessed at.",
                "page": None,
                "confidence": 0.0,
                "service_code": rule.get("service_code"),
                "type": rule.get("type"),
                "severity": rule.get("severity"),
                "flag": rule.get("flag"),
                "check_type": rule["check_type"],
            }
            # Deliberately NOT calling _explode_to_rows here -- if THAT
            # function (or _format_page_display) is itself what just
            # failed, calling it again with the fallback entry could fail
            # the same way and escape this except block entirely.
            rows = [{
                "rule_id": rule_id,
                "service_code": entry["service_code"],
                "type": entry["type"],
                "severity": entry["severity"],
                "flag": entry["flag"],
                "result": entry["result"],
                "confidence": entry["confidence"],
                "page": None,
                "detail": entry["evidence"],
            }]

        findings[rule_id] = entry
        export_rows.extend(rows)

        if entry["result"] in NEEDS_ACTION_RESULTS and entry.get("type") != "Informational":
            needs_review.append(rule_id)

    return {
        "findings": findings,
        "export_rows": export_rows,
        "needs_review": needs_review,
    }
