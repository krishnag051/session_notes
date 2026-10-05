"""Regression test for the "use real API" bug: Streamlit re-executes
app.py's whole script on every interaction, so a naive
`_REAL = judge_module._run_judgment_checks_once` captured at the top of
the script re-reads whatever that attribute CURRENTLY holds on every
rerun — and once the mocked default state has run once, that "capture"
permanently corrupts to the mock. This simulates exactly that
multi-rerun sequence and confirms the real function survives it.

Mocks only the actual outbound HTTP client (anthropic.Anthropic, blocked
by conftest.py's own autouse guardrail) — never a higher-level function
like _run_judgment_checks_once itself, since that's exactly the function
whose identity this bug is about.
"""
import pytest

from ..pipeline import judge as judge_module
from ..pipeline.model_boundary import resolve_judgment_boundary


def _mock_fn(judgment_rules, fields, rendered_images, tracker=None, call_reason="call", model_override=None):
    return {r["rule_id"]: {"result": "pass", "evidence": "MOCKED", "page": 1, "confidence": 0.0} for r in judgment_rules}


@pytest.fixture(autouse=True)
def _reset_judge_module_state():
    """Undo whatever this test does to judge_module's own attributes so
    other tests never see a stashed/mocked _run_judgment_checks_once."""
    original = judge_module._run_judgment_checks_once
    stash_attr = "_real_run_judgment_checks_once_original"
    had_stash = hasattr(judge_module, stash_attr)
    stash_value = getattr(judge_module, stash_attr, None)
    yield
    judge_module._run_judgment_checks_once = original
    if had_stash:
        setattr(judge_module, stash_attr, stash_value)
    elif hasattr(judge_module, stash_attr):
        delattr(judge_module, stash_attr)


def test_real_survives_a_prior_mocked_rerun_the_original_bug_scenario():
    """Simulates the EXACT sequence that triggered the real bug:
    rerun 1 (page load, both boxes unchecked -> mocked), THEN rerun 2
    (user ticks both boxes -> should now be real). Before the fix, rerun
    2's naive re-capture of "the real function" would have grabbed
    rerun 1's mock instead.
    """
    # Rerun 1: default state, both boxes unchecked.
    mocked_1 = resolve_judgment_boundary(judge_module, use_real_api=False, confirmed_real=False, mock_fn=_mock_fn)
    assert mocked_1 is True
    assert judge_module._run_judgment_checks_once is _mock_fn

    # Rerun 2: user ticks both boxes.
    mocked_2 = resolve_judgment_boundary(judge_module, use_real_api=True, confirmed_real=True, mock_fn=_mock_fn)
    assert mocked_2 is False
    # The critical assertion: this must be the REAL function, not the mock
    # from rerun 1 -- the exact corruption the original bug produced.
    assert judge_module._run_judgment_checks_once is not _mock_fn
    assert judge_module._run_judgment_checks_once.__name__ == "_run_judgment_checks_once"


def test_real_path_actually_reaches_the_real_call_site_not_the_mock_stub():
    """Proves it's not just A different function object, but genuinely
    THE real one -- calling it (with model_override=None, forcing the
    Anthropic path) must reach conftest.py's own blocked anthropic.Anthropic
    seam and raise, rather than silently returning the mock's canned
    findings with no error at all. If the mock stub had incorrectly stayed
    active, this call would return {"...": "MOCKED"} findings with no
    exception -- this test would then fail on the pytest.raises below,
    which is exactly what makes it a real regression test for this bug.
    """
    resolve_judgment_boundary(judge_module, use_real_api=False, confirmed_real=False, mock_fn=_mock_fn)
    resolve_judgment_boundary(judge_module, use_real_api=True, confirmed_real=True, mock_fn=_mock_fn)

    # Fix Round (2026-10-05): real_api_guard.ensure_real_api_calls_allowed()
    # now fires INSIDE _run_judgment_checks_once, before it ever reaches
    # anthropic.Anthropic() -- this is now the first guard reached (the
    # conftest-level anthropic.Anthropic patch below it is still real,
    # still active, defense-in-depth, just no longer the first one hit).
    with pytest.raises(RuntimeError, match="BLOCKED by real_api_guard"):
        judge_module._run_judgment_checks_once(
            judgment_rules=[{"rule_id": "R-1", "description": "test rule"}],
            fields={"pages": []}, rendered_images={},
            tracker=None, call_reason="test", model_override=None,
        )


def test_toggling_back_to_mocked_after_a_real_selection_still_works():
    resolve_judgment_boundary(judge_module, use_real_api=True, confirmed_real=True, mock_fn=_mock_fn)
    resolve_judgment_boundary(judge_module, use_real_api=False, confirmed_real=False, mock_fn=_mock_fn)
    assert judge_module._run_judgment_checks_once is _mock_fn


def test_use_real_api_alone_without_confirmation_still_mocks():
    # Ticking "use real API" without the separate confirmation checkbox
    # must still resolve to mocked -- both flags are required.
    mocked = resolve_judgment_boundary(judge_module, use_real_api=True, confirmed_real=False, mock_fn=_mock_fn)
    assert mocked is True
    assert judge_module._run_judgment_checks_once is _mock_fn
