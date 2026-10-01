"""CallTracker.estimated_cost_usd — closes a real blind spot: this
tracker previously counted CALLS but never estimated dollar cost, so
anything built on top of it (e.g. the backend's batch spend cap) was
blind to session_note_extraction.py's own real spend. All mocked usage
dicts here — zero real network calls, zero real spend.
"""
import pytest

from ..pipeline.call_tracker import (
    HAIKU_INPUT_COST_PER_MTOK,
    HAIKU_OUTPUT_COST_PER_MTOK,
    INPUT_COST_PER_MTOK,
    OUTPUT_COST_PER_MTOK,
)
from ..pipeline.model_provider import CallTracker, ModelCallError


def test_openrouter_calls_cost_nothing_regardless_of_token_volume():
    tracker = CallTracker()
    tracker.record(reason="test", provider="openrouter", model="nvidia/nemotron-3-ultra-550b-a55b:free",
                    usage={"input_tokens": 50_000, "output_tokens": 20_000})
    assert tracker.estimated_cost_usd == 0.0


def test_anthropic_fallback_calls_cost_at_haiku_rates_not_sonnet_rates():
    tracker = CallTracker()
    tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5",
                    usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    expected = HAIKU_INPUT_COST_PER_MTOK + HAIKU_OUTPUT_COST_PER_MTOK
    assert tracker.estimated_cost_usd == expected
    # Confirms this is genuinely Haiku pricing, not accidentally Sonnet's.
    assert expected != INPUT_COST_PER_MTOK + OUTPUT_COST_PER_MTOK


def test_anthropic_primary_calls_cost_at_sonnet_rates():
    tracker = CallTracker()
    tracker.record(reason="test", provider="anthropic", model="claude-sonnet-5",
                    usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert tracker.estimated_cost_usd == INPUT_COST_PER_MTOK + OUTPUT_COST_PER_MTOK


def test_mixed_provider_calls_sum_each_at_their_own_rate():
    tracker = CallTracker()
    tracker.record(reason="free call", provider="openrouter", model="nvidia/nemotron-3-ultra-550b-a55b:free",
                    usage={"input_tokens": 100_000, "output_tokens": 100_000})
    tracker.record(reason="fallback call", provider="anthropic-fallback", model="claude-haiku-4-5",
                    usage={"input_tokens": 500_000, "output_tokens": 200_000})
    expected = (500_000 / 1_000_000 * HAIKU_INPUT_COST_PER_MTOK) + (200_000 / 1_000_000 * HAIKU_OUTPUT_COST_PER_MTOK)
    assert round(tracker.estimated_cost_usd, 6) == round(expected, 6)


def test_estimated_cost_zero_before_any_call():
    assert CallTracker().estimated_cost_usd == 0.0


def test_check_before_call_allows_a_call_under_the_spend_cap():
    tracker = CallTracker(max_spend_usd=2.00)
    tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5",
                    usage={"input_tokens": 100_000, "output_tokens": 50_000})
    assert tracker.estimated_cost_usd < 2.00
    tracker.check_before_call()  # must not raise -- still under the cap


def test_check_before_call_refuses_once_spend_is_at_or_over_the_cap():
    """Urgent production ask: a hard, ENFORCED per-document spend cap, not
    just a monitoring/alert -- once already-spent cost is at/over the
    cap, the NEXT call must be refused before it happens, not after."""
    tracker = CallTracker(max_spend_usd=1.00)
    tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5",
                    usage={"input_tokens": 1_000_000, "output_tokens": 0})  # costs exactly $1.00
    assert tracker.estimated_cost_usd == 1.00
    with pytest.raises(ModelCallError, match=r"\$1\.00"):
        tracker.check_before_call()


def test_check_before_call_with_no_spend_cap_never_refuses_on_spend_alone():
    tracker = CallTracker()  # max_spend_usd=None -- no cap configured
    tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5",
                    usage={"input_tokens": 50_000_000, "output_tokens": 50_000_000})
    tracker.check_before_call()  # must not raise regardless of how much has been spent
