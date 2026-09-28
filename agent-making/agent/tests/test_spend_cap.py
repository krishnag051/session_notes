"""The $4 hard spend ceiling — proves it blocks a call BEFORE it reaches
the model client, using a mocked high-cost response. Zero real spend to
verify this works.
"""
from types import SimpleNamespace

import pytest

from ..pipeline.call_tracker import ApiCallTracker, ApiSpendCapExceeded


def test_default_ceiling_is_4_dollars():
    tracker = ApiCallTracker()
    assert tracker.max_spend_usd == 4.00


def test_ceiling_reads_env_var_when_max_spend_not_passed(monkeypatch):
    monkeypatch.setenv("MAX_REAL_SPEND_USD", "1.50")
    tracker = ApiCallTracker()
    assert tracker.max_spend_usd == 1.50


def test_explicit_max_spend_overrides_env_var(monkeypatch):
    monkeypatch.setenv("MAX_REAL_SPEND_USD", "1.50")
    tracker = ApiCallTracker(max_spend_usd=9.00)
    assert tracker.max_spend_usd == 9.00


def test_check_before_call_passes_when_projected_spend_is_under_the_cap():
    tracker = ApiCallTracker(max_spend_usd=4.00)
    tracker.check_before_call(estimated_max_tokens=1000)  # should not raise


def test_check_before_call_blocks_before_the_first_call_when_worst_case_would_exceed_cap():
    """First call of the session -- no real usage data yet, so the
    worst-case projection (full estimated_max_tokens as output) is what's
    checked. A tiny cap plus a huge estimated_max_tokens must block."""
    tracker = ApiCallTracker(max_spend_usd=0.01)
    with pytest.raises(ApiSpendCapExceeded, match=r"\$0\.01"):
        tracker.check_before_call(estimated_max_tokens=32000)


def test_a_real_mocked_high_cost_response_blocks_the_next_call_before_it_reaches_the_model():
    """Simulates one real (mocked) call that comes back with a huge token
    usage -- the kind of response that would blow the $4 cap on its own --
    then confirms the tracker refuses the NEXT call before any client is
    ever touched. No real network call anywhere in this test.
    """
    tracker = ApiCallTracker(max_spend_usd=4.00)
    tracker.check_before_call(estimated_max_tokens=32000)  # first call allowed

    # Mocked high-cost usage: 1,000,000 output tokens at $10/Mtok = $10 --
    # already over the $4 cap from this ONE (mocked, never-real) call.
    huge_usage = SimpleNamespace(input_tokens=10_000, output_tokens=1_000_000)
    tracker.record(reason="mocked huge call", rule_ids=["R-1"], usage=huge_usage)

    assert tracker.estimated_cost() > 4.00

    with pytest.raises(ApiSpendCapExceeded, match=r"exceed the \$4\.00"):
        tracker.check_before_call(estimated_max_tokens=32000)


def test_check_before_call_checks_call_count_cap_before_spend_cap():
    """The call-count cap (max_calls) and the spend cap are two independent
    checks -- confirm the call-count one still fires even when spend is
    nowhere near its own limit."""
    from ..pipeline.call_tracker import ApiCallCapExceeded

    tracker = ApiCallTracker(max_calls=0, max_spend_usd=1000.00)
    with pytest.raises(ApiCallCapExceeded):
        tracker.check_before_call()


def test_estimated_cost_zero_before_any_real_call():
    tracker = ApiCallTracker()
    assert tracker.estimated_cost() == 0.0
