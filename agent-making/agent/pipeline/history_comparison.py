"""NEW module (Phase 2) — no analog in the prior TP-review project, which
never had a "compare this note against this same person's prior notes"
concept at all (its patient identity was always a human-picked, pre-existing
record with no history to speak of).

Given a person's prior session documents (passed in as plain data — this
stays stateless, per this project's own "agent-making never touches the
database" boundary — see docs/backend-agent-making-plan.md), does the
actual copy-paste/identical-data-point similarity comparison, deterministically
and for zero cost, then builds `extra_context` for the small set of
rules.json rule_ids marked `needs_history: true` so judge.py's judgment call
can confirm/refine that deterministic signal rather than trust it blindly —
a real narrative similarity check is exactly the kind of thing a plain
string-similarity ratio can flag reliably, but "is this a legitimate
near-identical description of a genuinely similar session, or an
inappropriate copy-paste" still benefits from a real read.

`prior_extractions` shape: a list of {"date_of_service": str, "full_text":
str} — the prior session's own full extracted page text (the exact string
extract.py/session_note_extraction.py already produce; cheap to store,
nothing new to build to get it). Newest-first or any order — every entry is
compared, not just the most recent.
"""
from __future__ import annotations

from difflib import SequenceMatcher

# A rule_id's own current_extraction and prior_extractions are compared as
# whole documents (not narrative-only) — deliberately simple and honest
# about what it actually checks: real byte/word-level similarity of the
# extracted text, not a semantic "is this the same clinical content"
# judgment (that refinement is what judge.py's own judgment call, given
# this module's extra_context, is for).
_HIGH_SIMILARITY_THRESHOLD = 0.85

NOT_CHECKABLE_NO_HISTORY = {
    "result": "not_checkable",
    "evidence": (
        "No prior session data was available for this person to compare against — this rule "
        "genuinely cannot be checked without it, so it isn't guessed at."
    ),
    "page": None,
    "confidence": 0.0,
}


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def build_history_context(
    rule_ids: list[str],
    current_full_text: str,
    prior_extractions: list[dict] | None,
) -> dict[str, dict | None]:
    """Returns {rule_id: extra_context_dict_or_None} for every rule_id in
    `rule_ids` (the needs_history subset of the judgment batch). A None
    value means "no prior data — short-circuit this rule_id to
    NOT_CHECKABLE_NO_HISTORY, never send it to judge.py at all" (see
    api.py's own use of this function).
    """
    if not prior_extractions:
        return {rule_id: None for rule_id in rule_ids}

    best_match = None
    best_ratio = 0.0
    for prior in prior_extractions:
        prior_text = prior.get("full_text") or ""
        if not prior_text:
            continue
        ratio = _similarity(current_full_text, prior_text)
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = prior

    if best_match is None:
        # prior_extractions was non-empty but every entry had no usable
        # text — same honest "genuinely nothing to compare against" case
        # as no prior_extractions at all.
        return {rule_id: None for rule_id in rule_ids}

    context = {
        "comparison_type": "deterministic_text_similarity",
        "highest_similarity_ratio": round(best_ratio, 3),
        "compared_against_date_of_service": best_match.get("date_of_service"),
        "flagged_as_likely_copy_paste": best_ratio >= _HIGH_SIMILARITY_THRESHOLD,
        "note_to_model": (
            f"A deterministic text-similarity comparison against this person's {len(prior_extractions)} "
            f"prior session document(s) found the closest match at a {best_ratio:.0%} similarity ratio "
            f"(session dated {best_match.get('date_of_service')!r}). "
            + (
                "This is high enough to suggest the current note's narrative/data points may be "
                "copy-pasted rather than newly written — confirm this by reading both, don't just trust "
                "this number; a genuinely similar session (same routine goals, similar wording used "
                "independently) is not automatically a violation."
                if best_ratio >= _HIGH_SIMILARITY_THRESHOLD
                else "This is not high enough on its own to suggest copy-pasting, but still read the "
                "current note's own narrative/data points for signs of it independently."
            )
        ),
    }
    return {rule_id: context for rule_id in rule_ids}
