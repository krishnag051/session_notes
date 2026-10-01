"""The judgment layer: one real model call per batch of check_type=="judgment"
rules, forced through tool-use into the Findings schema — one entry required
per rule_id sent in. Self-consistency is handled by majority vote across
several independent calls (see run_judgment_checks_majority_vote), because
this model has no usable determinism knob (temperature is not configurable
on claude-sonnet-5 — confirmed, not assumed: passing a non-default value is
rejected as deprecated for this model) and per-call sampling variance is
real, not theoretical, for rules sitting near a genuine judgment boundary.

Lifted from the prior TP-review project's judge.py — same mechanism
(FINDINGS_TOOL shape, majority vote, evidence/result-contradiction guard,
page-citation enforcement), rewritten prompt content for session-note rules
instead of TP rules. Dropped: payor detection, plan-type detection,
previous-TP comparison — none of that applies here.
"""
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import anthropic
from dotenv import load_dotenv

from .model_provider import call_openrouter_with_fallback, resolve_provider_and_model

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

MODEL = "claude-sonnet-5"

# Empty to start (per this phase's own scope) — real per-call sampling
# variance hasn't been measured yet against this project's own rules. The
# mechanism exists so a rule_id confirmed to flip near-randomly even under
# majority vote can be pinned here later, same escape hatch the prior
# project needed after real measurement: a rule that never stabilizes is
# given a fixed, zero-cost "needs human review" finding instead of ever
# being asked again, rather than burning real calls on a question this
# model structurally can't answer consistently.
STABILIZED_UNCERTAIN_RULE_IDS: frozenset[str] = frozenset()

_STABILIZED_UNCERTAIN_FRAMING = (
    "Needs human review. We're still refining how this question is checked, so for now please "
    "confirm this item yourself rather than relying on an automated answer."
)


def stabilized_uncertain_finding(rule_id: str) -> dict:
    """A fixed, zero-cost finding for a rule_id pinned in
    STABILIZED_UNCERTAIN_RULE_IDS — never sent to the model at all."""
    return {
        "result": "uncertain",
        "evidence": _STABILIZED_UNCERTAIN_FRAMING,
        "page": None,
        "confidence": 0.0,
    }


FINDINGS_TOOL = {
    "name": "record_findings",
    "description": (
        "Record one finding per rule_id given in the judgment rule list. "
        "You must return exactly one entry for every rule_id provided — no "
        "more, no fewer."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {"type": "string"},
                        "evidence": {
                            "anyOf": [
                                {
                                    "type": "string",
                                    "description": (
                                        "A specific, quoted or closely-paraphrased justification grounded in "
                                        "the document. Never a restatement of the rule. Work out your "
                                        "reasoning here BEFORE choosing a result below — do not decide the "
                                        "result first and write justifying evidence afterward. State the "
                                        "finding directly and stop — one or two tight sentences, the minimum "
                                        "quote/reference needed to support it. No restating the question, no "
                                        "hedging preamble ('It appears that...', 'Upon review of...'), no "
                                        "throat-clearing before the actual point. If you need to cite a page "
                                        "number inside this text (in addition to the structured `page` field "
                                        "below), use exactly the tag [Page N] — e.g. '[Page 15]' — never "
                                        "'page 15', 'pages 15-18', a comma list, or any other phrasing."
                                    ),
                                },
                                {
                                    "type": "array",
                                    "description": (
                                        "Use this array form instead of a single string when the SAME problem "
                                        "shows up as a genuinely distinct, page-specific issue on more than one "
                                        "page — one entry per page, each naming that page's specific problem. "
                                        "Never collapse multiple pages into one summary sentence."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "page": {"type": "integer", "description": "1-indexed page number."},
                                            "detail": {
                                                "type": "string",
                                                "description": "The specific problem found on this exact page — not a shared generic description reused across pages.",
                                            },
                                        },
                                        "required": ["page", "detail"],
                                    },
                                },
                            ],
                        },
                        "result": {
                            "type": "string",
                            "enum": ["pass", "fail", "uncertain", "not_applicable", "not_checkable"],
                            "description": "Choose this AFTER writing the evidence above, and make sure it's the conclusion that evidence actually points to — not a categorical judgment made before working through the reasoning.",
                        },
                        "evidence_supports_result": {
                            "type": "boolean",
                            "description": (
                                "Must be true. Re-read your own evidence text and confirm it actually "
                                "supports the result you chose before setting this — if your evidence "
                                "describes something as absent, resolved, or not applicable, result "
                                "cannot be 'fail'; if it names an unresolved problem, result cannot be "
                                "'pass.' If you cannot honestly mark this true, change result to "
                                "'uncertain' instead of submitting a contradiction — do not set this "
                                "to false and submit anyway, it will be rejected and re-asked."
                            ),
                        },
                        "page": {
                            "anyOf": [
                                {"type": "integer", "description": "A single 1-indexed page number."},
                                {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 2,
                                    "description": (
                                        "Use this array form when the SAME finding genuinely depends on "
                                        "or references more than one specific page together. Only include "
                                        "pages actually load-bearing to this finding."
                                    ),
                                },
                                {
                                    "type": "null",
                                    "description": (
                                        "Not page-specific. For a fail/uncertain/not_checkable result "
                                        "specifically, treat null as a last resort, not a default."
                                    ),
                                },
                            ],
                            "description": (
                                "1-indexed page number(s) the evidence came from. Must be null when "
                                "evidence is the {page, detail} array form above (each item already "
                                "carries its own page). NON-PASS RESULTS (fail/uncertain/not_checkable): a "
                                "real, correct page number here is what lets a reviewer actually verify the "
                                "problem — do not leave this null just because it's more convenient."
                            ),
                        },
                        "confidence": {"type": "number", "description": "0.0-1.0"},
                        "nothing_relevant_found_anywhere": {
                            "type": "boolean",
                            "description": (
                                "Only meaningful when result is 'not_applicable' or 'not_checkable' -- "
                                "must be true ONLY if you searched and found NO real, relevant "
                                "information anywhere in the document for this rule. Set it to false if "
                                "you found SOME relevant information, even partial — in that case `page` "
                                "above must still cite where that partial information came from."
                            ),
                        },
                    },
                    "required": [
                        "rule_id", "evidence", "result", "evidence_supports_result", "page", "confidence",
                        "nothing_relevant_found_anywhere",
                    ],
                },
            }
        },
        "required": ["findings"],
    },
}


def _build_prompt(judgment_rules: list[dict], fields: dict, rendered_images: dict[int, bytes]) -> list[dict]:
    rules_summary = [
        {
            "rule_id": r["rule_id"],
            "service_code": r.get("service_code"),
            "description": r["description"],
            "notes": r.get("notes"),
            "params": r.get("params"),
            # Real, additional data for this specific rule — e.g. this
            # person's own prior extracted-field history, built by
            # history_comparison.py for the small set of rule_ids that need
            # it (needs_history: true in rules.json). None for every other
            # rule.
            "additional_real_data": r.get("extra_context"),
        }
        for r in judgment_rules
    ]

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "You are reviewing an ABA (Applied Behavior Analysis) session note against a set of "
                "compliance rules. For each rule below, determine pass / fail / uncertain / "
                "not_applicable / not_checkable, grounded in the actual document text and "
                "images provided — never guess, and use 'uncertain' rather than a confident-"
                "sounding guess when the evidence is genuinely ambiguous.\n\n"
                "Where a rule includes a 'params' object, treat those values as the exact, "
                "authoritative thresholds for that rule (e.g. a sentence-per-hour minimum or a time "
                "cap) — use them directly rather than re-deriving numbers from the prose description.\n\n"
                "Where a rule includes a non-null 'additional_real_data' value, that is real data "
                "collected specifically for this person (their own prior session extractions) — not "
                "part of this document itself, but genuinely real, current information you should "
                "actually compare against/reason with for that rule, not ignore.\n\n"
                "If a rule's own description or notes describe checking a value against a NAMED "
                "EXTERNAL SOURCE that is not itself provided to you anywhere in this prompt — a "
                "timesheet, a corresponding sibling document, a billing ledger, or any other named "
                "external system or document — you were not given that resource. Recognizing that a "
                "value LOOKS plausible from your own general knowledge is NOT the same as having "
                "actually checked it against the specific source the rule names. For a rule like "
                "this, report what you CAN genuinely verify from this document's own text, but set "
                "the result to 'uncertain' or 'not_checkable' for the rule's actual external-"
                "comparison claim, not a 'pass' that implies the named external source was checked. "
                "This does NOT apply to a rule that only needs you to read/reason about the "
                "document's OWN text (presence, internal consistency, narrative plausibility, etc.).\n\n"
                "Before finalizing each finding, check that your evidence text is consistent with the "
                "result you chose — if your evidence describes something as absent, resolved, or not "
                "applicable, the result cannot be 'fail'; if your evidence names an unresolved "
                "problem, the result cannot be 'pass.' Set evidence_supports_result to true only when "
                "this check genuinely passes. If it doesn't, change result to 'uncertain' (and update "
                "the evidence to match) rather than submitting a contradiction.\n\n"
                "If a rule's problem shows up as a distinct, page-specific issue on more than one "
                "page, set evidence to a list of {page, detail} objects — one entry per page — "
                "instead of a single summary string, and enumerate every page it actually recurs on, "
                "not just a representative sample.\n\n"
                "WRITING STYLE: write every evidence/detail string short and direct — state the "
                "finding, back it with the minimum quote or reference needed, then stop. No restating "
                "the rule/question, no hedging preamble, no throat-clearing.\n\n"
                "PAGE CITATIONS INSIDE EVIDENCE TEXT: if you name a page inside the evidence/detail "
                "text itself, use exactly one format, every time: the tag [Page N]. Never write "
                "'page 12', 'pages 12-14', 'p. 12', or a comma/range list.\n\n"
                "PAGE NUMBERS MATTER MOST ON NON-PASS RESULTS: for every fail/uncertain/not_checkable "
                "finding, make a genuine, specific effort to attach the real page(s) the problem is "
                "actually on before considering `page` null. If, after real effort, you still cannot "
                "identify a page, prefer downgrading a shaky-feeling 'fail' to 'uncertain' and say "
                "plainly in the evidence that no specific page could be identified.\n\n"
                "Rules to check (JSON):\n" + json.dumps(rules_summary, indent=2)
            ),
        },
        {"type": "text", "text": "Full extracted page text, in page order:"},
    ]

    for page in fields["pages"]:
        low_text_note = " [LOW TEXT — likely image-only; see rendered image if provided below]" if page.get("low_text") else ""
        content.append({
            "type": "text",
            "text": f"--- Page {page['page_number']}{low_text_note} ---\n{page['text']}",
        })

    if rendered_images:
        content.append({
            "type": "text",
            "text": (
                "Rendered images of pages that either have little/no extractable text, or "
                "contain content embedded as an image that the text above this line cannot "
                "capture, in page order:"
            ),
        })
        for page_number in sorted(rendered_images):
            content.append({"type": "text", "text": f"--- Rendered page {page_number} ---"})
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _b64(rendered_images[page_number]),
                },
            })

    return content


def _b64(data: bytes) -> str:
    import base64
    return base64.standard_b64encode(data).decode("utf-8")


MAX_TOKENS = 32000
OPENROUTER_MAX_TOKENS = 8000


def _flatten_prompt_text(content: list[dict]) -> str:
    """Collapses the multi-block prompt (text + rendered page images) into
    plain text for a provider that can't accept images — the OpenRouter
    free-tier default model is text-only. Only used on that path.
    """
    return "\n\n".join(b["text"] for b in content if b.get("type") == "text")


def _run_judgment_checks_once(
    judgment_rules: list[dict],
    fields: dict,
    rendered_images: dict[int, bytes],
    tracker=None,
    call_reason: str = "call",
    model_override: str | None = None,
) -> dict[str, dict]:
    """A single real judgment-layer call. Returns {rule_id: {"result",
    "evidence", "page", "confidence"}} — one entry per rule_id the model
    both answered and self-confirmed (see _findings_dict_from_list).

    `tracker` (an ApiCallTracker) is optional but should always be passed in
    production paths — the only thing standing between "run this" and
    silently making an unbounded number of real, billed API calls.

    `model_override=None` (every real production caller) uses the real,
    billed Anthropic path directly. Anything else resolves through
    model_provider.py's OpenRouter-default-with-Anthropic-fallback path —
    used for cheap/free development and testing, never the production
    default.
    """
    if not judgment_rules:
        return {}

    rule_ids = [r["rule_id"] for r in judgment_rules]

    content = _build_prompt(judgment_rules, fields, rendered_images)

    provider, model = ("anthropic", MODEL) if model_override is None else resolve_provider_and_model(model_override)

    if provider == "openrouter":
        if tracker is not None:
            tracker.check_before_call(estimated_max_tokens=OPENROUTER_MAX_TOKENS)
        if rendered_images:
            print(
                f"[judge] NOTE: {len(rendered_images)} rendered page image(s) present for this batch, but "
                f"the OpenRouter free-tier path is text-only — images are dropped for this call."
            )
        or_result = call_openrouter_with_fallback(
            model=model,
            prompt_text=_flatten_prompt_text(content),
            tool_name="record_findings",
            tool_description=FINDINGS_TOOL["description"],
            input_schema=FINDINGS_TOOL["input_schema"],
            max_tokens=OPENROUTER_MAX_TOKENS,
            call_reason=call_reason,
            tracker=tracker,
        )
        print(
            f"[judge] this judgment batch was served by provider={or_result['provider_used']!r} "
            f"model={or_result['model_used']!r} (reason={call_reason!r})."
        )
        if tracker is not None:
            tracker.record(reason=call_reason, rule_ids=rule_ids, usage=SimpleNamespace(**or_result["usage"]))
        tool_input = or_result["arguments"]
        if "findings" not in tool_input:
            raise RuntimeError(
                f"Judgment call ({or_result['provider_used']}:{or_result['model_used']}) returned without a "
                f"'findings' key; got keys: {list(tool_input.keys())}."
            )
        return _findings_dict_from_list(tool_input["findings"])

    if tracker is not None:
        tracker.check_before_call(estimated_max_tokens=MAX_TOKENS)

    # Explicit, bounded timeout -- see humanize.py's own real-bug comment
    # (same SDK, same 10-minute DEFAULT_TIMEOUT otherwise) for the full
    # story: a single slow/rate-limited call hanging up to 10 minutes was
    # confirmed as the real cause of a production review-time regression.
    # 120s here (vs. humanize's 30s) because this is a genuinely heavier
    # call -- a full judgment batch over multiple rules and page images,
    # not a short per-finding rewrite -- still far short of the default.
    client = anthropic.Anthropic(timeout=120.0, max_retries=1)

    # thinking disabled: this is a bounded classification/extraction task, not
    # open-ended reasoning, and Sonnet 5 runs adaptive thinking by default —
    # those tokens come out of the same max_tokens budget as the tool call
    # itself and can starve the JSON output before it completes.
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        thinking={"type": "disabled"},
        tools=[FINDINGS_TOOL],
        tool_choice={"type": "tool", "name": "record_findings"},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        response = stream.get_final_message()

    if tracker is not None:
        tracker.record(reason=call_reason, rule_ids=rule_ids, usage=response.usage)

    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Judgment call hit max_tokens ({MAX_TOKENS}) before finishing its tool "
            f"call — the findings JSON is truncated/incomplete. Raise judge.MAX_TOKENS "
            f"or send fewer judgment rules per call."
        )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError(
            f"No tool_use block in the judgment response (stop_reason={response.stop_reason!r}). "
            f"Content blocks returned: {[b.type for b in response.content]}."
        )
    if "findings" not in tool_use.input:
        raise RuntimeError(
            f"Judgment tool call returned without a 'findings' key "
            f"(stop_reason={response.stop_reason!r}); got keys: {list(tool_use.input.keys())}."
        )
    return _findings_dict_from_list(tool_use.input["findings"])


_RESULT_TO_NATURAL_PHRASE = {
    "pass": "pass",
    "fail": "fail",
    "uncertain": "this is uncertain",
    "not_applicable": "this doesn't apply",
    "not_checkable": "this can't be checked",
}


def _natural_result_phrase(result: str) -> str:
    return _RESULT_TO_NATURAL_PHRASE.get(result, result)


def _evidence_preview(evidence, *, max_len: int = 220) -> str:
    """One plain-text preview of a finding's own evidence, for folding into
    a disagreement summary — handles all 3 real shapes the evidence field
    can take (plain string, the {page, detail} multi-page list form, or a
    malformed non-string/non-list value), truncated so one side of a
    disagreement can't swamp the whole message."""
    text = _coerce_evidence_for_finding(evidence)
    if isinstance(text, list):
        text = " ".join(_coerce_evidence_to_string(item.get("detail", item)) if isinstance(item, dict) else str(item) for item in text)
    elif not isinstance(text, str):
        text = _coerce_evidence_to_string(text)
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def _short_uncertain_summary(entries: list[dict], *, split_desc: str) -> str:
    """A plain-English summary of a genuine disagreement across calls —
    surfaces each distinct result's own real, representative evidence text
    (with page, when available) rather than internal vote-count language
    ("3 of 5 calls said fail") a reviewer shouldn't need to parse.
    """
    groups: dict[str, dict] = {}
    for e in entries:
        result = e.get("result")
        if result not in groups or (e.get("page") is not None and groups[result].get("page") is None):
            groups[result] = e

    sides = []
    labels = ["One assessment", "A separate assessment", "Another assessment", "A further assessment"]
    for i, (result, entry) in enumerate(groups.items()):
        preview = _evidence_preview(entry.get("evidence"))
        page = entry.get("page")
        page_str = f" (page {page})" if page is not None else ""
        phrase = _natural_result_phrase(result)
        label = labels[i] if i < len(labels) else "Another assessment"
        if preview:
            sides.append(f"{label} concluded {phrase} -- \"{preview}\"{page_str}")
        else:
            sides.append(f"{label} concluded {phrase}{page_str}")

    if not sides:
        return "The automated review could not reach a clear, consistent answer for this item. Please confirm manually."
    summary = "This item genuinely came back uncertain: " + "; ".join(sides) + "."
    return f"{summary} Please confirm manually."


def _coerce_evidence_for_finding(evidence):
    """A legitimate str or list (FINDINGS_TOOL's two documented evidence
    shapes) passes through unchanged; anything else (a genuinely malformed
    response shape) is coerced to a safe string so a pass-through/winning
    branch never crashes downstream on an unexpected type."""
    if isinstance(evidence, (str, list)):
        return evidence
    return _coerce_evidence_to_string(evidence)


def _coerce_evidence_to_string(evidence) -> str:
    """The schema (FINDINGS_TOOL) is advisory to the model, not
    runtime-enforced on the response side — every finding-dict consumer in
    this file must coerce through this rather than silently trust the
    model's shape, since a malformed evidence value here would otherwise
    crash downstream (e.g. humanize.py, which expects a str)."""
    if isinstance(evidence, str):
        return evidence
    if isinstance(evidence, dict) and isinstance(evidence.get("detail"), str):
        return evidence["detail"]
    return json.dumps(evidence)


def _page_for_uncertain_fallback(entries: list[dict]):
    """Picks the first non-null page among disagreeing calls, in call
    order, so a synthetic "uncertain" finding still points a reviewer at a
    real page whenever any call found one."""
    for e in entries:
        if e.get("page") is not None:
            return e["page"]
    return None


def _fail_strictly_beats_uncertain(entries: list[dict]) -> dict | None:
    """Real bug found via a real Cazi 97151/SN-97151-09 CSV audit: two
    judges independently found the SAME underlying facts (this rule's own
    required-vs-actual component count) and both effectively concluded a
    real shortfall -- one just hedged and called it "uncertain" instead of
    committing to "fail". The old reconciliation treated that exactly like
    a genuine pass-vs-fail disagreement and defaulted to "uncertain",
    discarding a real, evidence-grounded majority.

    An "uncertain" vote is a judge declining to commit, not a vote FOR
    passing -- it carries no evidence that this should pass. So when the
    only results present are "fail" and "uncertain" (NO "pass" vote
    anywhere) and "fail" is the STRICT plurality among them, the honest
    answer is "fail", not a blanket "uncertain". Scoped narrowly on
    purpose: any real "pass" vote, or an exact fail/uncertain tie, still
    falls through to the ordinary uncertain fallback below -- a genuine
    pass-vs-fail split (a real question about which way this goes) is
    untouched.
    """
    counts = Counter(e["result"] for e in entries)
    fail_votes, uncertain_votes, pass_votes = counts.get("fail", 0), counts.get("uncertain", 0), counts.get("pass", 0)
    if pass_votes == 0 and fail_votes > 0 and fail_votes > uncertain_votes:
        fail_entries = [e for e in entries if e["result"] == "fail"]
        return (
            next((e for e in fail_entries if e.get("page") is not None), None)
            or next((e for e in fail_entries if not e.get("page_unresolved")), None)
            or fail_entries[0]
        )
    return None


def _two_way_uncertain_finding(f: dict, s: dict) -> dict:
    fail_winner = _fail_strictly_beats_uncertain([f, s])
    if fail_winner is not None:
        return {**fail_winner, "evidence": _coerce_evidence_for_finding(fail_winner["evidence"])}
    fallback_page = _page_for_uncertain_fallback([f, s])
    return {
        "result": "uncertain",
        "evidence": _short_uncertain_summary([f, s], split_desc="2-call disagreement"),
        "page": fallback_page,
        "confidence": 0.0,
        **({} if fallback_page is not None else {"page_unresolved": True}),
    }


def _three_way_majority_finding(f: dict, s: dict, t: dict) -> dict:
    """A strict majority (2 of 3 sharing the same result) wins, keeping
    whichever of the matching pair's own finding dict (evidence/page/
    confidence) carries a real page, if any do. No majority (all 3
    disagree) falls back to "uncertain" — a 3-way split is not this
    function's job to force a pick on.
    """
    entries = [f, s, t]
    counts = Counter(e["result"] for e in entries)
    winning_result, winning_count = counts.most_common(1)[0]
    if winning_count >= 2:
        winning_entries = [e for e in entries if e["result"] == winning_result]
        winner = (
            next((e for e in winning_entries if e.get("page") is not None), None)
            or next((e for e in winning_entries if not e.get("page_unresolved")), None)
            or winning_entries[0]
        )
        return {**winner, "evidence": _coerce_evidence_for_finding(winner["evidence"])}
    fallback_page = _page_for_uncertain_fallback(entries)
    return {
        "result": "uncertain",
        "evidence": _short_uncertain_summary(
            entries, split_desc="2-call disagreement plus its own tie-breaking 3rd call, no majority",
        ),
        "page": fallback_page,
        "confidence": 0.0,
        **({} if fallback_page is not None else {"page_unresolved": True}),
    }


def run_judgment_checks(
    judgment_rules: list[dict],
    fields: dict,
    rendered_images: dict[int, bytes],
    tracker=None,
    call_reason: str = "call",
    model_override: str | None = None,
) -> dict[str, dict]:
    """Self-consistency wrapper: calls _run_judgment_checks_once TWICE with
    identical inputs and reconciles. Where both calls agree on a rule_id's
    result, that result is kept. Where they disagree, ONE additional
    batched 3rd call covers just the disagreeing subset (never one extra
    call per disagreeing rule_id — all of them go in that single 3rd
    call), then majority-votes among all 3 answers for each. If the 3rd
    call fails to return an answer for one of those rule_ids, that rule_id
    falls back to the same two-call "uncertain" finding this function
    would have produced without the tie-break — never silently dropped.

    Used by integrity.py's own retry/page-recovery passes (a cheap 2-call
    check), not the initial batch — see run_judgment_checks_majority_vote
    below for the production initial-batch mechanism (5-way vote).
    """
    if not judgment_rules:
        return {}
    first = _run_judgment_checks_once(
        judgment_rules, fields, rendered_images, tracker=tracker,
        call_reason=f"{call_reason} (consistency check 1/2)", model_override=model_override,
    )
    second = _run_judgment_checks_once(
        judgment_rules, fields, rendered_images, tracker=tracker,
        call_reason=f"{call_reason} (consistency check 2/2)", model_override=model_override,
    )

    reconciled: dict[str, dict] = {}
    disagreed_ids: list[str] = []
    for rule_id in set(first) & set(second):
        f, s = first[rule_id], second[rule_id]
        if f["result"] == s["result"]:
            winner = f if f.get("page") is not None else (s if s.get("page") is not None else f)
            reconciled[rule_id] = {**winner, "evidence": _coerce_evidence_for_finding(winner["evidence"])}
        else:
            disagreed_ids.append(rule_id)

    if not disagreed_ids:
        return reconciled

    rules_by_id = {r["rule_id"]: r for r in judgment_rules}
    tiebreak_rules = [rules_by_id[rid] for rid in disagreed_ids]
    third = _run_judgment_checks_once(
        tiebreak_rules, fields, rendered_images, tracker=tracker,
        call_reason=f"{call_reason} (tie-break 3/3, {len(disagreed_ids)} disagreeing rule_id(s))",
        model_override=model_override,
    )

    for rule_id in disagreed_ids:
        f, s = first[rule_id], second[rule_id]
        t = third.get(rule_id)
        if t is None:
            reconciled[rule_id] = _two_way_uncertain_finding(f, s)
        else:
            reconciled[rule_id] = _three_way_majority_finding(f, s, t)
    return reconciled


def run_judgment_checks_majority_vote(
    judgment_rules: list[dict],
    fields: dict,
    rendered_images: dict[int, bytes],
    n_calls: int = 5,
    tracker=None,
    call_reason: str = "call",
    min_agreement: int | None = 4,
    model_override: str | None = None,
) -> dict[str, dict]:
    """The production initial-batch mechanism: n_calls (default 5)
    independent, concurrent calls with identical input; a result needs
    min_agreement (default 4-of-5) votes to be trusted, else the rule_id
    falls back to "uncertain". Default n_calls=5/min_agreement=4 mirrors
    the prior TP-review project's own measured choice (a 4-of-5 bar
    reliably captured real per-rule majorities that a cheaper 2-of-3
    vote didn't, at a materially better cost/benefit trade-off than a more
    expensive 7-way vote) — re-measure against this project's own rules
    before assuming the same numbers transfer perfectly.

    For each rule_id present in ALL n_calls responses: if min_agreement
    is met, that result wins (kept from whichever call first produced it,
    preferring one with a real page). Otherwise falls back to "uncertain".
    A rule_id missing from any single call is left out of the returned
    dict entirely — integrity.py's retry logic handles it.
    """
    if not judgment_rules:
        return {}
    with ThreadPoolExecutor(max_workers=n_calls) as pool:
        futures = [
            pool.submit(
                _run_judgment_checks_once,
                judgment_rules, fields, rendered_images, tracker=tracker,
                call_reason=f"{call_reason} (majority vote {i + 1}/{n_calls})", model_override=model_override,
            )
            for i in range(n_calls)
        ]
        all_results = [f.result() for f in futures]
    return _reconcile_majority_vote(all_results, min_agreement=min_agreement)


def _reconcile_majority_vote(
    all_results: list[dict[str, dict]], *, min_agreement: int | None = None,
) -> dict[str, dict]:
    if not all_results:
        return {}
    common_ids = set.intersection(*(set(r) for r in all_results))
    reconciled = {}
    for rule_id in common_ids:
        entries = [r[rule_id] for r in all_results]
        counts = Counter(e["result"] for e in entries)
        winning_result, winning_count = counts.most_common(1)[0]
        required = min_agreement if min_agreement is not None else (len(all_results) / 2)
        meets_bar = winning_count >= required if min_agreement is not None else winning_count > required
        if meets_bar:
            winning_entries = [e for e in entries if e["result"] == winning_result]
            winner = (
                next((e for e in winning_entries if e.get("page") is not None), None)
                or next((e for e in winning_entries if not e.get("page_unresolved")), None)
                or winning_entries[0]
            )
            reconciled[rule_id] = {**winner, "evidence": _coerce_evidence_for_finding(winner["evidence"])}
            continue
        fail_winner = _fail_strictly_beats_uncertain(entries)
        if fail_winner is not None:
            reconciled[rule_id] = {**fail_winner, "evidence": _coerce_evidence_for_finding(fail_winner["evidence"])}
            continue
        bar_desc = f"needed {int(required)}+ agreeing" if min_agreement is not None else "no majority"
        fallback_page = _page_for_uncertain_fallback(entries)
        reconciled[rule_id] = {
            "result": "uncertain",
            "evidence": _short_uncertain_summary(entries, split_desc=bar_desc),
            "page": fallback_page,
            "confidence": 0.0,
            **({} if fallback_page is not None else {"page_unresolved": True}),
        }
    return reconciled


def _findings_dict_from_list(findings_list: list[dict]) -> dict[str, dict]:
    """Converts the tool call's raw findings array into {rule_id: finding}.

    A finding the model itself marked evidence_supports_result=False is
    rejected, not recorded — left out of the returned dict, which makes it
    look identical to a rule_id the model dropped entirely. integrity.py's
    missing-rule_id retry logic (built for exactly that case) picks it back
    up and re-asks automatically.

    A finding with NO page number, that also isn't a genuine "nothing
    relevant found anywhere" case, is kept (not dropped) with
    `page_unresolved: True` — answer-acceptance (does this rule_id have a
    real, evidenced judgment) and page-citation (does that judgment also
    cite a page) are two independent questions; integrity.py's separate
    page-recovery pass handles the latter without ever risking a real,
    repeatedly-confirmed answer being thrown away and replaced with a
    guessed "not_checkable" just because a page was hard to pin down.

    Every non-dict entry in the findings array (a tool-call formatting
    slip, not reproduced deterministically but confirmed to occur) is
    treated the same way — dropped and retried as if missing, never a
    crash.
    """
    malformed = [f for f in findings_list if not isinstance(f, dict)]
    if malformed:
        print(
            f"[judge] Dropping {len(malformed)} malformed (non-dict) findings-array entr"
            f"{'y' if len(malformed) == 1 else 'ies'} (will be retried as if missing): {malformed!r}"
        )
    findings_list = [f for f in findings_list if isinstance(f, dict)]

    page_unresolved_ids = set()
    for f in findings_list:
        if f.get("page") is not None or isinstance(f.get("evidence"), list):
            continue
        result = f.get("result")
        if result in ("not_applicable", "not_checkable") and f.get("nothing_relevant_found_anywhere") is True:
            continue
        page_unresolved_ids.add(f.get("rule_id"))
    if page_unresolved_ids:
        print(
            f"[judge] {len(page_unresolved_ids)} finding(s) accepted without a page number (no "
            f"confirmed 'nothing relevant found anywhere' either) -- keeping the real judgment, "
            f"flagged for a separate page-recovery retry rather than discarded: {sorted(page_unresolved_ids)}"
        )

    rejected = [f for f in findings_list if not f.get("evidence_supports_result", False)]
    if rejected:
        print(f"[judge] Rejecting {len(rejected)} finding(s) with evidence_supports_result=False (will be retried as if missing):")
        for f in rejected:
            evidence = f.get("evidence")
            evidence_str = evidence if isinstance(evidence, str) else json.dumps(evidence)
            print(
                f"  - {f.get('rule_id')!r}: result={f.get('result')!r}, "
                f"confidence={f.get('confidence')!r}, evidence={evidence_str!r}"
            )

    return {
        f["rule_id"]: {
            "result": f["result"],
            "evidence": f["evidence"],
            "page": f.get("page"),
            "confidence": f.get("confidence"),
            **({"page_unresolved": True} if f["rule_id"] in page_unresolved_ids else {}),
        }
        for f in findings_list
        if f.get("evidence_supports_result", False)
    }
