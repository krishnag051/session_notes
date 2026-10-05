"""Fix Round (2026-08-27), Part 4: a real, distinct, testable pass that
rewrites already-generated evidence/context text for tone and length right
before a result is returned -- ma'am's team's real, recurring feedback:
this text reads too long, too clinical/AI-sounding, and overuses commas
and special characters, not how a person would actually explain it.

DELIBERATE CHOICE, flagged rather than silently decided: this is a plain
DETERMINISTIC rewrite (regex-based cleanup), not a new LLM call. A fully
general "make arbitrary long prose sound like a person said it out loud"
rewrite genuinely wants a language model in the general case -- this
round's own cost constraint (no new real Anthropic calls, and a new LLM
call site at all is a real spend/design decision even on the free
OpenRouter tier) is the reason a model-based version wasn't built or run
here. What this DOES do, for real, on every evidence string this pipeline
already produces:
  - Strips a trailing ".0" off whole numbers (a Python float artifact --
    "8.0" reads like a person wrote "8", never "eight point zero").
  - Converts a bracketed Python list repr (`['1578293197']`) into plain
    prose ("1578293197", or "1578293197 and 9999999999" for two).
  - Lowercases a mid-sentence "AND"/"OR" that was only capitalized because
    it was written as a boolean-logic connective, not for real emphasis.
  - Drops a small set of confirmed-redundant clinical filler
    parentheticals that just restate a fact already stated plainly right
    before them (e.g. "(inclusive)" right after a date range that's
    already unambiguous without it).
  - Normalizes semicolons to periods (a real sentence break, not a
    clinical list-joiner) and collapses doubled punctuation/whitespace
    left behind by the above.

Deliberately NEVER touches: a `[Page N]` citation tag (judge.py's own
exact format, load-bearing for the frontend's clickable page links), a
quoted verbatim source_quote, or any digit sequence that isn't a whole-
number float artifact (a real date, a real ID, a real percentage stays
exactly as reported -- this is a tone/length rewrite, never a fact
change, per this round's own explicit instruction).
"""
from __future__ import annotations

import re

# Preserves [Page N] tags untouched -- see judge.py::EXACT_PAGE_TAG_RE,
# duplicated here (not imported) so this module has no import-time
# dependency on judge.py, matching this pipeline's existing convention of
# small, independently-testable modules (e.g. schedule_hours.py,
# session_note_comparison.py) that don't reach into judge.py.
_PAGE_TAG_RE = re.compile(r"\[Page \d{1,4}\]")

_WHOLE_NUMBER_FLOAT_RE = re.compile(r"(?<!\d)(\d+)\.0\b")
_PYTHON_LIST_REPR_RE = re.compile(r"\[\s*((?:'[^']*'|\"[^\"]*\")(?:\s*,\s*(?:'[^']*'|\"[^\"]*\"))*)\s*\]")
_LIST_ITEM_RE = re.compile(r"['\"]([^'\"]*)['\"]")
# Lowercases a mid-sentence "AND"/"OR" -- anywhere EXCEPT right at the very
# start of the string or right after a sentence-ending ". " (both of which
# are real sentence starts, where it's grammatically a real capital, not a
# boolean-logic artifact).
_MIDSENTENCE_BOOLEAN_WORD_RE = re.compile(r"(?<!^)(?<!\. )(\bAND\b|\bOR\b)")

# Confirmed-redundant, safe to drop outright -- each of these only ever
# appears immediately after the exact fact it restates (checked against
# every current real call site in fields.py/schedule_hours.py/
# session_note_comparison.py, not a general ban on the phrase everywhere).
_REDUNDANT_PARENTHETICALS = (
    " (inclusive)",
)


def _delist(match: "re.Match[str]") -> str:
    items = _LIST_ITEM_RE.findall(match.group(1))
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# Fix Round (Round 12) -- REAL ROOT CAUSE FOUND: real evidence from a
# live run against mc_current.pdf showed raw "PAGEREF20"/"PAGEREF01"-style
# text leaking into reviewer-facing evidence on several rules (QA-BIP-13,
# QA-GIP-25, QA-GIP-29, QA-SIG-02). Round 9 fixed the WRONG mechanism twice
# over: (1) it stripped this pattern from the SOURCE PDF's own extracted
# text (pipeline/extract.py) -- confirmed directly against the real
# document that this pattern never appears in the source text at all, so
# there was nothing there to strip; (2) it hardened humanize_evidence_
# with_llm's own protect/restore safety net -- confirmed by grepping every
# real call site in this package that this function is NEVER actually
# called by the production pipeline (api.py only ever calls the plain,
# deterministic humanize_evidence below) -- so that whole fix was dead
# code from the moment it was written. The real source: judge.py's own
# prompt explicitly instructs the model to cite pages using the tag
# "[Page N]" -- the model, instead of following that instruction, sometimes
# writes its own Word-cross-reference-shaped "PAGEREFnn" text directly into
# its evidence string (confirmed real shape: it gets worse, not better, the
# more pages a single finding cites -- QA-GIP-29's real 16-page citation is
# the densest of the four affected rules). This is real model behavior this
# codebase cannot prevent by instruction alone; it can only be caught and
# neutralized before a reviewer ever sees it. Fixed at the one place every
# real finding's evidence already passes through unconditionally
# (api.py::_to_review_result calling this exact function) -- safe to just
# strip the malformed token outright (not try to recover a real page number
# from it): the finding's own structured `page` field is computed
# independently and already carries the real citation, so nothing is lost
# by removing the model's own redundant, malformed mention of it from the
# prose.
_PAGEREF_ARTIFACT_RE = re.compile(r"\s*,?\s*PAGEREF\d+(?!X)")


def humanize_evidence(text: str) -> str:
    """Pure function, no I/O, no model call -- safe to run on every
    evidence string unconditionally. Idempotent (running it twice gives
    the same result as running it once), so it's safe to apply at more
    than one point in the pipeline without double-mangling text.

    Splits the text on [Page N] tags first and only transforms the
    segments BETWEEN them, so a tag is never itself touched or split.
    """
    if not text:
        return text

    text = _PAGEREF_ARTIFACT_RE.sub("", text)
    segments = _PAGE_TAG_RE.split(text)
    tags = _PAGE_TAG_RE.findall(text)

    cleaned_segments = [_humanize_segment(seg) for seg in segments]

    result = cleaned_segments[0]
    for tag, seg in zip(tags, cleaned_segments[1:]):
        result += tag + seg
    return result.strip()


def _humanize_segment(text: str) -> str:
    text = _PYTHON_LIST_REPR_RE.sub(_delist, text)
    text = _WHOLE_NUMBER_FLOAT_RE.sub(r"\1", text)
    text = _MIDSENTENCE_BOOLEAN_WORD_RE.sub(lambda m: m.group(1).lower(), text)
    for phrase in _REDUNDANT_PARENTHETICALS:
        text = text.replace(phrase, "")
    # Semicolon -> period: a real sentence break reads more like speech
    # than a clinical list-joiner. Re-capitalize the word right after, to
    # keep the new sentence looking like a real sentence.
    text = re.sub(
        r";\s*([a-z])",
        lambda m: ". " + m.group(1).upper(),
        text,
    )
    # Collapse whitespace left behind by any of the above -- deliberately
    # NOT .strip()'d here: this runs per-segment, between [Page N] tags
    # that keep their own surrounding spaces in the segments beside them
    # (see humanize_evidence's own docstring) -- stripping a segment's own
    # leading/trailing space here would eat the real space next to an
    # adjacent tag once rejoined. The whole result gets one real .strip()
    # in humanize_evidence, after rejoining.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([.,])", r"\1", text)
    return text


# ---------------------------------------------------------------------------
# Next Round (2026-08-27), Part 1: the real LLM rewrite, approved with an
# explicit $4 hard cap for this whole round including testing.
#
# Model choice: claude-haiku-4-5 -- this repo's own already-established
# cheap/fast real-Anthropic model (see model_provider.py's
# ANTHROPIC_FALLBACK_MODEL, same model, same reasoning: "a fallback whose
# whole point is get a real answer anyway shouldn't itself become the
# slow/expensive path" -- a short rewrite task is the same shape of
# problem). Real, current pricing: $1.00/Mtok input, $5.00/Mtok output.
#
# This is a SECOND pass, layered ON TOP of the free, deterministic
# humanize_evidence above -- never a replacement. Order matters: the
# deterministic pass runs first (kills float artifacts, list-reprs, etc.
# for $0), THEN this function sends the already-cleaned text to the model
# for the harder tone/length rewrite the deterministic pass can't do
# generically.
#
# [Page N] tags are protected structurally, not just by instruction: every
# tag is swapped for a plain, unmistakable placeholder token BEFORE the
# text ever reaches the model, and swapped back verbatim AFTER -- the
# model literally never sees the string "[Page" at all, so it can't
# rephrase, drop, or renumber a tag even if it tried.
import anthropic

from .real_api_guard import ensure_real_api_calls_allowed

REWRITE_MODEL = "claude-haiku-4-5"
_INPUT_COST_PER_MTOK = 1.00
_OUTPUT_COST_PER_MTOK = 5.00

# REDESIGNED after a real, caught-cheap test run: an earlier version of
# this used exotic private-use Unicode characters (e.g. "\ue0000\ue001")
# as placeholders. Real result against claude-haiku-4-5: it did NOT copy
# them through -- it "explained" them as if they were meaningful content
# ("in location 0", "location 1 came up empty"), silently destroying the
# real page reference. Confirmed by literally running it (cost: under a
# tenth of a cent) before committing to this design, not assumed. Switched
# to a plain, unmistakable ASCII token instead -- a small, cheap model is
# far more likely to recognize "keep PAGEREF0 exactly as-is" as a literal
# copy instruction than to treat oddball Unicode as something to describe.
# Fix Round (2026-08-27): REAL BUG FOUND AND FIXED, confirmed via a real
# completed review (30 of 181 findings across a wide, unrelated spread of
# rule_ids showed raw, un-restored "PAGEREF0PAGEREF1PAGEREF2"-style tokens
# directly in reviewer-facing evidence). Root cause: the OLD template
# ("PAGEREF{i}") relied on \b word-boundary anchors on both sides, which
# silently fails whenever two placeholders sit directly adjacent with no
# separator -- e.g. a source evidence string with "[Page 1][Page 4][Page
# 71]" (no whitespace between citations, a real, common shape -- tie-
# break/merge summaries in particular concatenate sub-evidence strings
# that may already end and begin with a page tag) becomes
# "PAGEREF0PAGEREF1PAGEREF2" once protected. Confirmed directly:
# re.compile(r"\bPAGEREF(\d+)\b").findall("PAGEREF0PAGEREF1PAGEREF2")
# returns [] -- a digit immediately followed by the next token's leading
# "P" is a word-char-to-word-char transition, so \b never matches there,
# for ANY of the tokens except possibly ones at the very start/end of the
# whole string. _restore_page_tags's own re.sub() silently performs ZERO
# substitutions in this case (no error raised) -- the placeholders simply
# pass through unrestored. WORSE: the safety net below couldn't catch it
# either, for the exact same reason (it uses this same regex to scan for
# stray tokens) -- an empty findall() looks identical to "nothing to worry
# about," not "the tokens are unreadable." Fixed by making the template
# self-delimiting -- a fixed non-digit terminator ("X") after the number
# means adjacent tokens are still unambiguous with NO boundary check
# needed at all, so this can never happen regardless of how many
# citations are adjacent or where in the string they fall.
_PLACEHOLDER_TEMPLATE = "PAGEREF{i}X"
_PLACEHOLDER_RE = re.compile(r"PAGEREF(\d+)X")
# Real bug found via a real manual audit round: a not_checkable rule's own
# `evidence` is rules.json's own `notes` field verbatim (see api.py) --
# this is the exact, recognizable shape of a developer-facing cross-
# reference comment ("Same reasoning as SN-97151-17."), never a real
# finding with its own facts to preserve. See humanize_evidence_with_llm's
# own pre-call guard for why this is skipped before ever reaching the model.
_CROSS_REFERENCE_ONLY_RE = re.compile(r"^Same reasoning as [A-Za-z0-9\-]+\.?$", re.IGNORECASE)

_REWRITE_SYSTEM_PROMPT = (
    "You rewrite a compliance checklist's evidence text so it reads the way a BCBA would explain the finding "
    "to a colleague out loud, in one breath. Rules, no exceptions:\n"
    "1. Keep every real fact already in the text -- every date, number, name, and page reference. Never drop, "
    "round, or change a fact. This is a tone and length rewrite, never a summary that loses information.\n"
    "2. Some tokens look like PAGEREF0X, PAGEREF1X, etc. (always ending in the letter X). These are literal "
    "placeholder tokens standing in for a page citation. Treat each one as an opaque, unbreakable word -- copy it "
    "through byte-for-byte, in the same position relative to the surrounding words, exactly as spelled (same digits, same capitalization, no space "
    "inside it). Never describe, explain, translate, renumber, or refer to what a PAGEREF token 'means' -- it is "
    "not a location number or an index for you to interpret, it is a literal string you must reproduce unchanged.\n"
    "3. SHORT. Your rewrite's word count must not exceed the original's word count -- shorter is the goal, "
    "never longer. If the original is one sentence, your rewrite is one sentence, not a longer sentence "
    "stitched together with 'and'/'which'/'that'. Cut connecting words; don't add them.\n"
    "4. Minimal punctuation -- avoid semicolons, em-dashes, and bracket/parenthetical asides where a plain "
    "sentence says the same thing. Do not join two facts into one comma-heavy sentence with 'and' or 'which' -- "
    "either keep them as two short separate sentences, or drop a connecting clause that isn't adding new "
    "information.\n"
    "5. Example of the target style -- input: 'Patient age 17. Authorization range 07/30/2026 to 10/30/2026 "
    "(92 days) matches the expected 13-week range for age > 13.' Good output (keeps every fact, still short): "
    "'Patient's 17, and the 07/30/2026 to 10/30/2026 range matches the 13-week expectation for that age.' "
    "Bad output (too long, too many joined clauses, even though it also keeps the facts): 'The patient is 17 "
    "years old and the authorization runs from 07/30/2026 to 10/30/2026 for 92 days, which is the right "
    "13-week range we'd expect for someone over 13.'\n"
    "6. ALWAYS include the actual reason/conclusion, never just the setup fact. If the input has a contrast "
    "structure ('X is true, but Y contradicts it' / 'X; however, Y'), the clause after 'but'/'however'/"
    "'whereas'/'yet' is almost always the real reason this passed, failed, or came back uncertain -- it must "
    "survive the rewrite even if you compress or drop part of the setup clause before it. A reader must be "
    "able to tell WHY from your rewrite alone, not just see a neutral fact with no conclusion attached. Never "
    "end mid-sentence, mid-question, or with a dangling open quote -- every rewrite is a complete thought.\n"
    "7. Example with several names in one finding -- input: \"Only patient name 'Jane Doe' and AKA 'Janie' "
    "appear. Provider 'Pat Smith' and BCBA 'Robin Lee' are labeled as providers, not treated as patient aliases.\" "
    "Good output (every name stays with its own original role, nothing merged): \"Only Jane Doe and her AKA Janie "
    "show up as the patient -- Pat Smith and Robin Lee are labeled providers, not patient aliases.\" Bad output "
    "(two different people's names fused into one phrase -- never do this): \"Patient's Jane Doe with AKA Robin "
    "Lee Janie.\"\n"
    "8. Output ONLY the rewritten text itself -- the rewritten finding, nothing about the task of rewriting it. "
    "No preamble, no meta-commentary, no acknowledgment of these instructions, no quotes around it, nothing else. "
    "If the input text already contains everything needed to rewrite (which it always does), begin your reply "
    "with the rewrite itself, not a statement that you understood the instructions or are ready to begin.\n"
    "9. Cut reflexive hedging about the review's OWN process or confidence -- a trailing aside like 'though a "
    "specific page couldn't be confirmed, but the result is accurate' reads like an apology, not a finding. This "
    "is different from rule 1 (keep every real fact about the patient/session/document): a remark about what "
    "THIS SYSTEM could or couldn't confirm, as opposed to a fact about the note itself, is process commentary, "
    "not a fact to preserve -- drop it unless it is the actual reason the rule passed, failed, or is uncertain. "
    "Example -- input: 'The session ran 45 minutes, though a specific page couldn't be confirmed, but the result "
    "is accurate.' Good output (the real fact stated plainly, the hedge gone): 'The session ran 45 minutes.' Bad "
    "output (keeps apologizing for the system's own limitation): 'The session ran 45 minutes, though a specific "
    "page could not be confirmed for this finding.'"
)


def _protect_page_tags(text: str) -> tuple[str, list[str]]:
    tags: list[str] = []

    def _replace(m: "re.Match[str]") -> str:
        tags.append(m.group(0))
        return _PLACEHOLDER_TEMPLATE.format(i=len(tags) - 1)

    return _PAGE_TAG_RE.sub(_replace, text), tags


def _restore_page_tags(text: str, tags: list[str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: tags[int(m.group(1))], text)


def humanize_evidence_with_llm(
    text: str, *, client: "anthropic.Anthropic | None" = None, tracker=None,
) -> tuple[str, dict]:
    """Runs the free deterministic pass first, then one real Haiku call for
    the tone/length rewrite. Returns (rewritten_text, usage) where usage is
    {"input_tokens": int, "output_tokens": int, "cost_usd": float} -- real,
    measured numbers from the real response, never estimated after the
    fact. `client` is injectable for tests (mock the boundary, same
    standing convention as every other real-Anthropic test in this repo);
    `None` (the default, every real caller) constructs a real
    anthropic.Anthropic().

    `tracker` (an ApiCallTracker) is optional but should always be passed
    in production (humanize_findings_batch, the real caller, does) --
    checked before the real call below and recorded after, same discipline
    as every other real call site in this pipeline. Without one, this
    makes an uncapped real call.

    Returns the deterministically-cleaned text UNCHANGED (usage all zeros)
    for empty/None input or input with no non-tag content to rewrite --
    never spends a real call on nothing (and never checks the tracker for
    a call that was never going to happen anyway).

    `usage["pre_humanize_text"]` (Next Round, Part 2) is always the
    deterministically-cleaned text this call started from -- the real
    "before" a caller needs to keep alongside the rewritten "after" for a
    real audit trail (e.g. a CSV export column), regardless of whether the
    rewrite below succeeded or was rejected by the page-ref safety net.
    """
    cleaned = humanize_evidence(text)
    if not cleaned or not cleaned.strip():
        return cleaned, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "pre_humanize_text": cleaned}

    protected, tags = _protect_page_tags(cleaned)

    # Fix Round (Jacob Freund 10-2026-U1), Item 18: REAL BUG FOUND AND
    # FIXED -- the emptiness guard above only checks `cleaned`, BEFORE
    # page tags are protected into opaque PAGEREFnX placeholder tokens.
    # Evidence that is nothing but a page citation (e.g. just "[Page 4]")
    # passes that check (cleaned.strip() is non-empty) but reduces to a
    # bare placeholder token with ZERO real sentence content once
    # protected -- sent to the model like that, there is genuinely
    # nothing to rewrite. Root-caused against this round's own confirmed
    # real bug (a leaked, paraphrased system-prompt response -- "I'm
    # ready to rewrite compliance checklist evidence text... I don't see
    # the actual evidence text to rewrite yet") -- this is exactly the
    # shape of input that provokes it. Guard on the PROTECTED text (what
    # is actually sent), not the pre-protection text.
    if not _PLACEHOLDER_RE.sub("", protected).strip():
        return cleaned, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "pre_humanize_text": cleaned}

    # REAL BUG FOUND AND FIXED (real manual audit round): a not_checkable
    # rule's own `evidence` is rules.json's own `notes` field verbatim
    # (see api.py's own not_checkable judgment_results construction) --
    # for a rule whose notes are just a developer-facing cross-reference
    # ("Same reasoning as SN-97151-17."), there is genuinely nothing
    # substantive to rewrite. Confirmed on a real batch: sent as-is, Haiku
    # correctly recognized it had no real finding to work with and
    # responded with its own first-person clarification/refusal request
    # ("I need the original text of SN-97151-17 to rewrite this...") --
    # which then got stored and shown to a reviewer AS IF it were the
    # finding. Skipped before ever reaching the model, same as the
    # empty-input/placeholder-only guards above -- genuinely nothing to
    # humanize, not a rewrite that then needs rejecting after the fact.
    if _CROSS_REFERENCE_ONLY_RE.match(cleaned.strip()):
        return cleaned, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "pre_humanize_text": cleaned}

    if tracker is not None:
        tracker.check_before_call()
    # REAL BUG FOUND AND FIXED (urgent production regression: reviews went
    # from under a minute to 10+ minutes the moment this pass started
    # making real calls): the anthropic SDK's own DEFAULT_TIMEOUT is 10
    # MINUTES per call (confirmed directly in the installed package:
    # anthropic/_constants.py's DEFAULT_TIMEOUT = httpx2.Timeout(timeout=
    # 10*60, connect=5.0)), with DEFAULT_MAX_RETRIES=2 on top of that. A
    # single slow/rate-limited Haiku call among the many this batch now
    # makes could legitimately hang for up to that full window before
    # even failing -- and since humanize_findings_batch's per-finding
    # isolation only catches the exception AFTER it happens, nothing made
    # that failure happen FAST. One stuck call dominates the whole
    # batch's wall-clock time even though every other call already
    # finished. A short, explicit timeout make a stuck/rate-limited call
    # fail fast instead -- per-finding isolation then falls back to raw
    # text for THAT finding only, same as any other failure, rather than
    # the whole document silently waiting minutes on one call. 30s is
    # comfortably above this pass's own real measured latency for a
    # short rewrite (a few seconds); max_retries=1 (not the SDK's own
    # default of 2) keeps one single call from compounding into multiple
    # multi-second retries on top of that.
    if client is None:
        ensure_real_api_calls_allowed("humanize.humanize_evidence_with_llm")
    real_client = client or anthropic.Anthropic(timeout=30.0, max_retries=1)
    # Fix Round (2026-08-27): REAL BUG FOUND AND FIXED -- confirmed on a
    # real completed review, several genuinely long tie-break/merge
    # disagreement summaries (which concatenate 2-3 calls' own evidence
    # into one string) got cut off mid-sentence with no ellipsis or any
    # indication, because the rewrite call's own max_tokens=200 was too
    # small for a real 3-way summary's rewritten length, and nothing
    # checked whether the response actually finished. Raised to a
    # genuinely comfortable ceiling for the longest real inputs this
    # pipeline produces (a 3-way disagreement summary joining three real
    # evidence strings) -- still bounded, just no longer tight enough to
    # bite on a real, common case.
    response = real_client.messages.create(
        model=REWRITE_MODEL,
        max_tokens=500,
        system=_REWRITE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": protected}],
    )
    rewritten_protected = "".join(b.text for b in response.content if b.type == "text")

    input_tokens = getattr(response.usage, "input_tokens", 0)
    output_tokens = getattr(response.usage, "output_tokens", 0)
    cost_usd = (input_tokens / 1_000_000) * _INPUT_COST_PER_MTOK + (output_tokens / 1_000_000) * _OUTPUT_COST_PER_MTOK
    usage = {
        "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd,
        "pre_humanize_text": cleaned,
    }
    if tracker is not None:
        # "anthropic-fallback" -- the SAME provider key model_provider.py's
        # own CallTracker already prices at claude-haiku-4-5's real rate
        # ($1.00/$5.00 per Mtok), never Sonnet's -- REWRITE_MODEL IS
        # claude-haiku-4-5, so this is the correct, not an approximate,
        # rate for this call.
        tracker.record(
            reason="humanize_evidence_with_llm", provider="anthropic-fallback", model=REWRITE_MODEL,
            usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        )

    # Hard safety net, not just a hopeful instruction: every PAGEREF token
    # sent in must come back in the response, exactly once each, or this
    # rewrite is rejected outright and the (still real, still cleaned-up)
    # deterministic-only text is returned instead. A rewrite that tightens
    # tone but loses a page reference is a regression, not an improvement
    # -- never ship one, no matter how good the rest of the rewrite reads.
    #
    # Fix Round (2026-08-27): REAL BUG FOUND AND FIXED, confirmed via an
    # actual crash on a real staging upload -- this used to check only
    # ONE direction (every real token present, exactly once) and never
    # checked the other: the model inventing a STRAY, out-of-range token
    # that was never sent to it (e.g. echoing PAGEREF4 when only
    # PAGEREF0-PAGEREF3 existed). That passed the old check fine (every
    # real token was still there, exactly once), so _restore_page_tags
    # went ahead and crashed with an uncaught IndexError trying
    # tags[4] on a 4-element list. Now checks BOTH directions before
    # ever calling _restore_page_tags: every expected token present
    # exactly once, AND every placeholder-shaped token actually in the
    # response has an index that's genuinely in range.
    expected_tokens = [_PLACEHOLDER_TEMPLATE.format(i=i) for i in range(len(tags))]
    all_expected_present_once = all(rewritten_protected.count(tok) == 1 for tok in expected_tokens)
    found_indices = [int(m) for m in _PLACEHOLDER_RE.findall(rewritten_protected)]
    has_stray_out_of_range_token = any(i < 0 or i >= len(tags) for i in found_indices)
    # Fix Round (Round 12) -- REAL BUG FOUND AND FIXED, confirmed via a
    # real live run against mc_current.pdf: real evidence leaked raw
    # "PAGEREF20", "PAGEREF25"..."PAGEREF29", "PAGEREF01" text -- missing
    # the trailing "X" every genuinely-protected token always has. This
    # is NOT the round-trip this safety net was built to check (a real
    # protected token that came back malformed or out of range) -- it's
    # the model either dropping the "X" off a real token in a dense,
    # many-citation sentence (confirmed real shape: QA-GIP-29 packs 16
    # page citations into one sentence), or inventing its OWN "PAGEREFnn"
    # notation for a plain "page nn" mention that was never protected at
    # all (zero real [Page N] tags existed in the input, so `tags` was
    # empty and every check above vacuously passed). Either way,
    # `_PLACEHOLDER_RE` (which requires the trailing X) never sees this
    # shape, so `found_indices`/`has_stray_out_of_range_token` are blind
    # to it -- and `_restore_page_tags` leaves it untouched since it only
    # substitutes matches of that same X-suffixed pattern. Checked
    # independently of `tags`/`found_indices`: ANY "PAGEREF" immediately
    # followed by digits and NOT immediately followed by "X" is
    # malformed by definition, real tag or hallucinated, protected or
    # not -- there is no legitimate shape this safety net should ever
    # let through that looks like this.
    has_malformed_pageref_token = bool(re.search(r"PAGEREF\d+(?!X)", rewritten_protected))
    # Fix Round (2026-08-27): REAL BUG FOUND AND FIXED -- a response cut
    # off by hitting max_tokens mid-sentence (Bug 2, above) could still
    # pass BOTH the checks above if the cutoff happened to land after
    # every page tag was already restated (a genuinely real case -- a
    # trailing clause with no further citation in it). The two checks
    # above alone are not a complete safety net; a truncated response is
    # its own real problem regardless of whether the page tags survived,
    # and must never ship silently either.
    was_truncated = response.stop_reason == "max_tokens"
    # Fix Round (Jacob Freund 10-2026-U1), Item 18: second, belt-and-
    # suspenders safeguard -- catches a leaked/paraphrased system-prompt
    # response even if the input-side guard above somehow doesn't (e.g. a
    # future prompt change, a different degenerate-input shape not
    # anticipated here). A real rewrite of real evidence never talks
    # ABOUT the rewriting task itself in the first person -- it just is
    # the rewritten evidence. Reject outright, fall back to the (still
    # real, still cleaned-up) deterministic-only text, same as every
    # other rejection path here -- never surface this to a reviewer.
    # BROADENED (real manual audit round): the pre-call cross-reference
    # guard above handles the ONE confirmed input shape that provokes
    # this, but a refusal/clarification response is inherently open-ended
    # in HOW it's phrased -- a real batch showed at least 9 distinct real
    # wordings ("I need the original text...", "I can't rewrite this
    # without...", "Unable to process this request...", "I appreciate the
    # instruction, but I can't complete...", "I don't have access to the
    # document...", etc.), all for the identical input. Anchored at the
    # START of the response (after whitespace) -- a real rewrite of real
    # evidence describes the PATIENT/session/finding, it does not open by
    # talking about ITSELF or the rewriting task in the first person.
    # Anchoring avoids false-positiving on a legitimate finding that
    # happens to mention e.g. "unable" mid-sentence ("patient was unable
    # to complete the task").
    # Rule-agnostic on purpose (Round 5 re-verification): this exact
    # refusal/meta-acknowledgment shape has now hit TWO unrelated rules in
    # two rounds (SN-97153-18's cross-reference text, then SN-97153-01's
    # name-dense finding after the Priority 2 prompt rule was added) --
    # it's a property of the MODEL's behavior under certain input shapes,
    # not something tied to one specific rule_id, so detection stays
    # anchored on the RESPONSE's own opening words regardless of which
    # rule produced the input. "I understand"/"I'm ready" cover this
    # round's own real observed openers (all 8 real responses started
    # with one of these two, even the one that briefly described real
    # content before pivoting to "Please provide the text to rewrite").
    is_leaked_prompt_meta_response = bool(re.match(
        r"\s*(?:"
        r"I understand\b|I'm ready\b|I am ready\b|"
        r"I need the (?:actual|original)\b|I don'?t have (?:access|the)\b|I do not have (?:access|the)\b|"
        r"I can'?t (?:rewrite|complete)\b|I cannot rewrite\b|I'm unable to\b|I am unable to\b|"
        r"Unable to (?:process|rewrite)\b|I don'?t see (?:the|any)\b|I do not see (?:the|any)\b|"
        r"I appreciate the instruction\b|Please provide\b|Please share\b|Could you provide\b|"
        r"I'm ready to rewrite\b|I am ready to rewrite\b"
        r")",
        rewritten_protected, re.IGNORECASE,
    )) or bool(re.search(
        r"\b(?:send|waiting for) the (?:text|compliance checklist(?:'s)? (?:evidence )?text) to rewrite\b"
        r"|\bwaiting for the compliance checklist text\b",
        rewritten_protected, re.IGNORECASE,
    ))
    if (
        all_expected_present_once and not has_stray_out_of_range_token and not has_malformed_pageref_token
        and not was_truncated and not is_leaked_prompt_meta_response
    ):
        rewritten = _restore_page_tags(rewritten_protected, tags).strip()
        usage["rejected_missing_page_ref"] = False
        usage["rejection_reason"] = None
        return rewritten, usage
    usage["rejected_missing_page_ref"] = True
    # Next Round (2026-08-27), item 3: a specific, loud reason, not just a
    # boolean -- lets a caller log/report WHY a rewrite was rejected
    # (missing vs. stray vs. truncated are different failure modes worth
    # telling apart), rather than only knowing that it was.
    if was_truncated:
        usage["rejection_reason"] = "truncated"
    elif has_stray_out_of_range_token:
        usage["rejection_reason"] = "stray_page_ref"
    elif has_malformed_pageref_token:
        usage["rejection_reason"] = "malformed_page_ref"
    elif is_leaked_prompt_meta_response:
        usage["rejection_reason"] = "leaked_prompt_meta_response"
    else:
        usage["rejection_reason"] = "missing_page_ref"
    return cleaned, usage


# Fix Round: this pass used to be a free, deterministic string-truncation
# heuristic living entirely in the FRONTEND (cut at the last clause
# boundary before a length budget, promoted to a period). Real bug found
# via a real manual audit: for the extremely common "X is true, but Y
# contradicts it" shape every FAIL reason takes, that heuristic cut right
# at the comma before "but", keeping only the neutral setup fact and
# silently dropping the entire reason the rule failed. A mechanical
# string-truncation approach structurally cannot tell "safe to cut" from
# "the conclusion is right here" -- this round replaces it with the SAME
# real, already-built LLM rewrite above (humanize_evidence_with_llm),
# following this project's own reference implementation (the prior
# TP-review project's app/agent_client.py::humanize_findings): run ONCE
# per finding, AFTER the full review already has its real, raw reasoning
# text for every rule -- never inline during rule-checking itself, and
# never blocking the rest of the review on a single finding's own rewrite.
#
# Bounded concurrency, not one worker per finding -- a real review can
# have 15-40 findings; firing all of them at Anthropic at once isn't
# necessary and risks real rate limits. 8 concurrent calls, same starting
# point the reference project settled on.
_HUMANIZE_MAX_WORKERS = 8


def humanize_findings_batch(
    texts: list[str],
    *,
    max_calls: int | None = None,
    max_spend_usd: float | None = None,
    labels: list[str] | None = None,
    client: "anthropic.Anthropic | None" = None,
) -> list[tuple[str, str, dict]]:
    """Batch entry point — ONE shared CallTracker across every finding in a
    single document's review, so a single document's humanize pass can
    never run away into an unbounded number of real calls (same
    statelessness convention as review_person_document itself: the caller
    supplies the cap, this function stays free of backend config). Uses
    model_provider.CallTracker specifically (NOT call_tracker.
    ApiCallTracker, which is Sonnet-pricing-specific and would misprice a
    Haiku call) — it already prices "anthropic-fallback" calls at Haiku's
    real rate, and is already built thread-safe (a lock around every
    count/token increment) for exactly this "many concurrent workers share
    one tracker" shape. Returns one (raw_text, humanized_text, usage)
    tuple per input text, same order as `texts`.

    Per-finding failure isolation: one finding's own rewrite failing (a
    bad model response, the page-ref safety net rejecting it, the cap
    being hit partway through the batch) falls back to (text, text, {}) —
    that finding's own raw text, unchanged — for THAT finding only. Every
    other finding in the same batch keeps its own real result, unaffected
    — a single bad response must never discard an entire batch of
    already-paid-for real rewrites (the exact real bug the reference
    project's own humanize_findings was built to fix).

    `labels` (optional, one per text — the caller passes each finding's
    own rule_id) makes a failure loud and specific when it's logged, not
    just "something in this batch failed". Falls back to a positional
    index when not given.
    """
    from concurrent.futures import ThreadPoolExecutor

    from .model_provider import CallTracker

    tracker = CallTracker(max_calls=max_calls, max_spend_usd=max_spend_usd)
    results: list[tuple[str, str, dict] | None] = [None] * len(texts)

    def _process_one(i: int, text: str) -> tuple[str, str, dict]:
        # REAL BUG FOUND AND FIXED (urgent production crash, confirmed via
        # a real traceback: "ValueError: not enough values to unpack
        # (expected 3, got 2)" on call #22 of a real batch): humanize_
        # evidence_with_llm returns a 2-TUPLE (humanized_text, usage) — its
        # own documented shape. This function's own contract (and every
        # caller's, including the except branch right below) is a
        # 3-TUPLE (raw_text, humanized_text, usage). The success path used
        # to return humanize_evidence_with_llm's result DIRECTLY, with no
        # reshaping at all — correct on every FAILURE (which separately,
        # explicitly returns a 3-tuple) but silently wrong on every
        # SUCCESS, for as long as this function has existed. usage["pre_
        # humanize_text"] is that 2-tuple's own real "raw" value — see
        # humanize_evidence_with_llm's own docstring.
        try:
            humanized_text, usage = humanize_evidence_with_llm(text, client=client, tracker=tracker)
            # Real gap found via a real manual audit round: a REJECTED
            # rewrite (truncated/stray-page-ref/malformed-page-ref/leaked-
            # meta-response) returns NORMALLY here -- no exception at all,
            # just humanized_text falling back to the deterministic-only
            # text with usage["rejection_reason"] set. The except branch
            # below was the ONLY place this function ever logged anything,
            # so a rejection (as opposed to an outright API
            # error/timeout/exception) was invisible -- no way to tell
            # "this finding genuinely had nothing to rewrite" apart from
            # "the rewrite was silently rejected for some other reason"
            # after the fact. Logged here too, distinctly, so the two
            # cases stay tellable apart in production logs going forward.
            if usage.get("rejection_reason"):
                label = labels[i] if labels else f"finding at index {i}"
                print(
                    f"[humanize] rewrite REJECTED for {label!r} (reason={usage['rejection_reason']!r}), "
                    f"falling back to the deterministic-only text for this finding only."
                )
            return usage.get("pre_humanize_text", text), humanized_text, usage
        except Exception as exc:  # noqa: BLE001 — isolate THIS finding only, see docstring above
            label = labels[i] if labels else f"finding at index {i}"
            print(f"[humanize] rewrite failed for {label!r}, falling back to raw text for this finding only: {exc}")
            return (text, text, {})

    if not texts:
        return []
    with ThreadPoolExecutor(max_workers=min(_HUMANIZE_MAX_WORKERS, len(texts))) as pool:
        futures = {pool.submit(_process_one, i, text): i for i, text in enumerate(texts)}
        for future in futures:
            i = futures[future]
            results[i] = future.result()
    return results  # type: ignore[return-value] -- every slot is filled by the loop above
