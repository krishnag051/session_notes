"""Step 6 of the pipeline (Section 4): rule-id coverage check. Diffs the
rule_ids returned by the judgment layer against the rule_ids sent. A gap is
a real problem, not a warning — retried, and never silently guessed at.

REAL BUG FOUND AND FIXED (2026-08, live incident): a persistently-missing
rule_id (confirmed case: QA-GIP-11) used to raise IntegrityError all the
way up through run_full_pipeline -> pipeline.api.review_treatment_plan,
which turned the ENTIRE review into `{"status": "failed"}` -- discarding
every other rule_id's real, already-computed, perfectly good finding along
with it. A real backend upload lost all 143 other rules' results over ONE
rule the judgment layer couldn't confidently answer after retrying. That's
a wildly disproportionate failure mode: the fix is not to give up and
throw away everything, and it's also not to guess a pass/fail for the
stubborn rule -- it's to mark ONLY that rule_id "not_checkable" (an honest
"couldn't determine," the same vocabulary this pipeline already uses for
every other genuine "no confident answer" case) and let every other rule's
real result through untouched. See run_judgment_with_integrity_check's own
docstring below for the mechanics.

Fix Round (2026-09-11 evening) -- REAL REGRESSION FOUND AND FIXED: page-
number enforcement (an earlier round this same day) made judge.py drop a
finding that answered the rule correctly but couldn't cite a page, same as
a genuinely-missing rule_id -- so a rule that reaffirmed "pass" on every
single retry, just never with a page, looked identical to one the model
never answered at all, and after `max_retries` got silently converted to
"not_checkable" -- discarding a real, repeatedly-confirmed judgment.
Confirmed live: QA-BIO-17, QA-GIP-32, QA-GIP-14, QA-GIP-28, QA-AI-02,
QA-AI-03, QA-AI-04 all did this on one real document. judge.py no longer
drops these (see `_findings_dict_from_list`'s own docstring) -- they now
come back flagged `page_unresolved: True` instead of being absent
entirely, so this module's missing-rule_id retry/exhaustion logic below
never sees them as missing in the first place. What THIS module adds on
top: `_run_page_recovery_pass`, a separate, bounded retry specifically for
the page citation, that can only ever ADD a page to an already-accepted
answer -- never override, downgrade, or flip the `result` it's attached
to. This is deliberate for stability (see that function's own docstring):
letting a page-only retry also relitigate the substantive verdict would
reintroduce exactly the kind of run-to-run flip the Judgment Layer
Stability round already fixed once.
"""
from . import judge

# Fix Round (2026-09-15), "Language Regression": REAL FIX -- this
# reviewer-facing text used to name internal mechanism ("self-consistency
# check", "evidence_supports_result=false"), the exact kind of jargon
# ma'am flagged by name. Rewritten in plain English -- same real meaning
# (we tried more than once and still couldn't get a confirmed answer, so
# we're marking it Not checkable instead of guessing), no internal terms.
NOT_CHECKABLE_AFTER_RETRIES_TEMPLATE = (
    "We were unable to determine a confirmed answer for this item after {attempts} attempt(s). "
    "Marked Not checkable rather than guessing."
)

PAGE_UNAVAILABLE_NOTE = (
    " (A specific page could not be confirmed for this finding -- the result itself is still accurate.)"
)


class IntegrityError(Exception):
    """Retained for callers/tests that want to distinguish this failure
    mode by type, and for genuinely catastrophic cases (e.g. every single
    rule_id missing, which would mean something is badly broken with the
    call itself, not just one hard rule) -- see
    run_judgment_with_integrity_check's own docstring for exactly when
    this still raises vs. when it degrades gracefully instead.
    """


def missing_rule_ids(sent_rule_ids: list[str], results: dict[str, dict]) -> list[str]:
    return [rid for rid in sent_rule_ids if rid not in results]


def run_judgment_with_integrity_check(
    judgment_rules: list[dict],
    fields: dict,
    rendered_images: dict[int, bytes],
    max_retries: int = 2,
    tracker=None,
    model_override: str | None = None,
) -> dict[str, dict]:
    """Calls judge.run_judgment_checks, and on any missing rule_id, retries
    only for the missing subset, up to max_retries times.

    FIXED (live incident, 2026-08): if any rule_id is STILL missing after
    exhausting retries, this used to raise IntegrityError unconditionally
    -- which propagated all the way up and discarded every OTHER rule_id's
    real, already-computed finding along with it (one stubborn rule
    nuking a real, paid-for review of everything else). Now:
    - If EVERY sent rule_id is missing (0 real answers came back at all),
      that's a sign the call mechanism itself is broken, not that one
      hard rule tripped up the model -- still raises IntegrityError, same
      as before, since there's nothing real to salvage.
    - Otherwise, each rule_id still missing after max_retries gets a real,
      honest "not_checkable" finding (NOT_CHECKABLE_AFTER_RETRIES_TEMPLATE)
      instead of a raised exception -- never a guessed pass/fail, and
      never silently dropped either (this print line, plus the finding's
      own distinctive evidence text, make it visible both in logs and in
      the final result). Every other rule_id's real answer is returned
      untouched.

    `tracker` (an ApiCallTracker) is forwarded to every real call this makes
    — the initial one and every retry. This is the ONLY place retries are
    triggered, so it's the one place that must never make a real call
    without checking the tracker's cap first (judge.py checks too, but the
    reason string here is what makes the resulting log line tell you *why*
    a given call happened, not just that it did).

    `model_override` (Round 61) is forwarded unchanged to every judge.py
    call this makes — defaults to None, which keeps this function's
    behavior identical to before this round for every caller that doesn't
    pass it (see judge.py's own docstring on this same parameter).

    Fix Round (Judgment Layer Stability) -- the INITIAL batch call now
    goes through `judge.run_judgment_checks_majority_vote` (5-way vote,
    4-of-5 required to commit to an answer, else "uncertain") instead of
    the old 2-call-plus-conditional-3rd-tie-break `run_judgment_checks`.
    This round's own real measurement (a pool of 7 independent raw calls
    against a real document, see this round's report) found the OLD
    2-3-sample mechanism was genuinely too small a window for several
    rules sitting at a real ~70-80%-of-the-time majority in their raw
    per-call distribution -- 4-of-5 reliably captures that real majority
    where 2-of-3 didn't, at a measured flip-rate reduction close to what a
    more expensive 7-way/5-of-7 vote achieved (see this round's own
    report for the real numbers from both configurations) -- chosen over
    7-way for the better cost/benefit trade-off. The retry pass below
    (for rule_ids still missing after the initial batch) is UNCHANGED,
    still `run_judgment_checks` -- that's a different problem (a rule_id
    the model dropped/rejected entirely, not a disagreement to vote on),
    and keeping it as a cheap 2-call retry (rather than a 5-call one)
    keeps the missing-rule-id recovery path's own cost from ballooning
    for what's usually a small handful of rule_ids.
    """
    sent_ids = [r["rule_id"] for r in judgment_rules]
    results = judge.run_judgment_checks_majority_vote(
        judgment_rules, fields, rendered_images, tracker=tracker, call_reason="initial batch",
        model_override=model_override, n_calls=5, min_agreement=4,
    )

    attempt = 0
    while True:
        missing = missing_rule_ids(sent_ids, results)
        if not missing:
            return _run_page_recovery_pass(judgment_rules, fields, rendered_images, results, tracker=tracker, model_override=model_override)
        attempt += 1
        if attempt > max_retries:
            if len(missing) >= len(sent_ids):
                raise IntegrityError(
                    f"Judgment layer failed to return ANY of the {len(sent_ids)} rule_id(s) sent, "
                    f"after {max_retries} retries. Rejecting — this looks like the call mechanism "
                    f"itself is broken, not one hard rule, so there is nothing real to salvage."
                )
            print(
                f"[integrity] {len(missing)} rule_id(s) never returned a confirmed answer after "
                f"{max_retries} retries: {missing}. Marking not_checkable and returning every OTHER "
                f"rule_id's real result — NOT raising, so one stubborn rule doesn't discard everything "
                f"else this review already correctly computed."
            )
            for rule_id in missing:
                results[rule_id] = {
                    "result": "not_checkable",
                    "evidence": NOT_CHECKABLE_AFTER_RETRIES_TEMPLATE.format(attempts=max_retries + 1),
                    "page": None,
                    "confidence": 0.0,
                }
            return _run_page_recovery_pass(judgment_rules, fields, rendered_images, results, tracker=tracker, model_override=model_override)
        retry_rules = [r for r in judgment_rules if r["rule_id"] in missing]
        retry_results = judge.run_judgment_checks(
            retry_rules,
            fields,
            rendered_images,
            tracker=tracker,
            call_reason=f"retry {attempt}/{max_retries} (missing or evidence_supports_result=false in previous response)",
            model_override=model_override,
        )
        results.update(retry_results)


def _run_page_recovery_pass(
    judgment_rules: list[dict],
    fields: dict,
    rendered_images: dict[int, bytes],
    results: dict[str, dict],
    *,
    tracker=None,
    model_override: str | None = None,
    max_page_retries: int = 2,
) -> dict[str, dict]:
    """Fix Round (2026-09-11 evening) -- the "maximize real page coverage"
    half of this round's fix. `results` at this point already has a real,
    accepted answer for every sent rule_id (this function's caller only
    reaches here once the missing-rule_id loop is done) -- some of those
    answers are flagged `page_unresolved: True` by judge.py because no
    page could be cited for them yet. This makes up to `max_page_retries`
    additional, cheap 2-call attempts asking specifically for those
    rule_ids again, hoping for a page this time.

    STABILITY, non-negotiable (see this module's own docstring, and the
    Judgment Layer Stability round this must not undo): a retry here can
    only ever ADD a page to an already-decided `result` -- it can never
    change what that result IS. A retry response is adopted only when its
    own `result` matches the one already accepted; if the retry disagrees
    on the substance (a different result) OR still can't cite a page
    either, the ORIGINAL accepted answer is kept completely unchanged.
    This makes the pass idempotent with respect to `result`: running it
    zero, one, or `max_page_retries` times can only ever affect whether a
    page is attached, never which rule_ids are pass/fail/uncertain/etc --
    so it cannot itself become a new source of run-to-run flips.

    After `max_page_retries` attempts, any rule_id still `page_unresolved`
    keeps its real, already-accepted result with `page: None`, and gets a
    short, honest note appended to its evidence (only when evidence is a
    plain string; the {page, detail} list form already explains itself)
    -- the same "accept the answer, mark the page as legitimately
    unavailable" pattern this round's fix requires, instead of the
    not_checkable-discard this replaces.
    """
    unresolved_ids = [rid for rid, r in results.items() if r.get("page_unresolved")]
    attempts_made = 0
    for attempt in range(1, max_page_retries + 1):
        if not unresolved_ids:
            break
        attempts_made = attempt
        retry_rules = [r for r in judgment_rules if r["rule_id"] in unresolved_ids]
        retry_results = judge.run_judgment_checks(
            retry_rules,
            fields,
            rendered_images,
            tracker=tracker,
            call_reason=f"page-recovery {attempt}/{max_page_retries} ({len(unresolved_ids)} rule_id(s) missing a page)",
            model_override=model_override,
        )
        still_unresolved = []
        for rule_id in unresolved_ids:
            retried = retry_results.get(rule_id)
            if retried is None:
                still_unresolved.append(rule_id)  # dropped this attempt -- keep the original, try again
                continue
            if retried["result"] != results[rule_id]["result"]:
                # A different verdict this time is a genuine disagreement,
                # not a page-only refinement -- never adopted here (that
                # would make this pass a hidden second vote on the
                # substance, reintroducing run-to-run instability). Keep
                # the original answer untouched either way.
                still_unresolved.append(rule_id)
                continue
            if retried.get("page_unresolved"):
                still_unresolved.append(rule_id)  # same verdict, still no page -- try again
                continue
            results[rule_id] = retried  # same verdict, now with a real page (or a legit exemption) -- adopt it
        unresolved_ids = still_unresolved

    if unresolved_ids:
        print(
            f"[integrity] {len(unresolved_ids)} rule_id(s) kept their real answer but never got a "
            f"page after {attempts_made} page-recovery attempt(s) -- accepting the answer with an "
            f"honest 'page unavailable' note rather than discarding it: {unresolved_ids}"
        )
    for rule_id in unresolved_ids:
        finding = results[rule_id]
        note = PAGE_UNAVAILABLE_NOTE.format(attempts=attempts_made + 1)  # +1: the initial attempt too
        if isinstance(finding["evidence"], str):
            finding["evidence"] = finding["evidence"] + note
        finding.pop("page_unresolved", None)

    # Clear the internal bookkeeping flag from every OTHER finding too --
    # it's how this pass tracks its own work, never part of the public
    # finding shape callers/tests further downstream should see.
    for finding in results.values():
        finding.pop("page_unresolved", None)

    return results
