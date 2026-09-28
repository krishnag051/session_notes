"""Resolves whether the judgment layer's model boundary should be mocked
or real, for app.py's "use the real Anthropic API" + confirmation checkbox
pair. Pulled out of app.py itself so this logic is unit-testable without a
Streamlit runtime — app.py's own top-level script code can't be imported by
a plain test (its Streamlit calls need an active script context).

BUG FOUND AND FIXED (Phase 2 follow-up): Streamlit re-executes app.py's
entire script top to bottom on every interaction — it is not a normal
module imported once. The original code did
`_REAL_RUN_JUDGMENT_CHECKS_ONCE = judge_module._run_judgment_checks_once`
as a plain top-level assignment, re-read on every rerun. Since the default
checkbox state (both unchecked) mocks the judgment layer on the very first
render, every later rerun "captured" the MOCK function as if it were the
real one — permanently corrupting that reference from the second rerun
onward. Ticking both real-API boxes afterward then still resolved back to
the (corrupted) mock, which is exactly the reported symptom: `api_calls: 0`
and the mocked placeholder evidence text even with both boxes checked.

Fixed by stashing the TRUE original on the judge module itself, under an
attribute name this code never reassigns, guarded so the stash only ever
happens once per process — every later call reads that stash back, never
whatever `_run_judgment_checks_once` currently (possibly already mocked)
holds.
"""
_STASH_ATTR = "_real_run_judgment_checks_once_original"


def resolve_judgment_boundary(judge_module, *, use_real_api: bool, confirmed_real: bool, mock_fn) -> bool:
    """Sets `judge_module._run_judgment_checks_once` to either the real
    function or `mock_fn`, based on the two checkbox states. Returns
    `mocked` (True if the mock was selected) so the caller can log/display
    which path this run actually took.
    """
    if not hasattr(judge_module, _STASH_ATTR):
        setattr(judge_module, _STASH_ATTR, judge_module._run_judgment_checks_once)
    real_fn = getattr(judge_module, _STASH_ATTR)

    mocked = not (use_real_api and confirmed_real)
    judge_module._run_judgment_checks_once = mock_fn if mocked else real_fn
    print(
        f"[model-boundary] mocked={mocked} (use_real_api={use_real_api}, confirmed_real={confirmed_real}) — "
        f"judge_module._run_judgment_checks_once is now {judge_module._run_judgment_checks_once.__name__!r}"
    )
    return mocked
