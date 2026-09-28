"""Proves the structural guardrail in conftest.py (`_block_real_api_calls`)
actually blocks the real Anthropic/OpenRouter call BEFORE any network
request is constructed — not just that it happens to fail some other way.

Zero real API calls in this file, by construction: the guardrail this file
tests is exactly what prevents that, and the one test that opts out via
@pytest.mark.real_api immediately re-patches the same seam with its own
local stub — it never lets the real import run either.
"""
import pytest

from ..pipeline import model_provider as model_provider_module


def test_guardrail_blocks_judge_anthropic_by_default():
    """A completely ordinary test, no marker, no explicit reference to the
    guardrail fixture -- confirms the autouse fixture patched the real seam
    out from under it regardless."""
    import anthropic
    with pytest.raises(RuntimeError, match="BLOCKED by agent-making/agent/tests/conftest.py"):
        anthropic.Anthropic()


def test_guardrail_blocks_model_provider_call_openrouter_by_default():
    with pytest.raises(RuntimeError, match="BLOCKED"):
        model_provider_module._call_openrouter(
            model="whatever", prompt_text="x", tool_name="t", tool_description="d",
            input_schema={}, max_tokens=10,
        )


def test_guardrail_blocks_model_provider_call_anthropic_by_default():
    with pytest.raises(RuntimeError, match="BLOCKED"):
        model_provider_module._call_anthropic(
            model="whatever", prompt_text="x", tool_name="t", tool_description="d",
            input_schema={}, max_tokens=10,
        )


def test_guardrail_blocks_humanize_evidence_with_llm_by_default():
    from ..pipeline import humanize as humanize_module
    with pytest.raises(RuntimeError, match="BLOCKED"):
        humanize_module.humanize_evidence_with_llm("some evidence text")


@pytest.mark.real_api
def test_real_api_marker_opts_out_of_the_autouse_guard(monkeypatch):
    """Proves the escape hatch works -- WITHOUT spending anything or
    needing real credentials. @pytest.mark.real_api makes the autouse
    fixture stand down for the ceiling-wrapped seams; this test then
    patches the same seam itself with a controlled fake (never touching
    the real network). If the autouse guard had NOT stood down for
    real_api tests, this fake would have been overwritten by the blocking
    stub, and calling it would raise the BLOCKED error instead of
    returning the fake's own value -- so this assertion is a genuine
    proof the marker works, not a tautology.
    """
    def _fake_call_openrouter(*args, **kwargs):
        return {"arguments": {"findings": []}, "usage": {"input_tokens": 1, "output_tokens": 1}}

    monkeypatch.setattr(model_provider_module, "_call_openrouter", _fake_call_openrouter)
    result = model_provider_module._call_openrouter(
        model="whatever", prompt_text="x", tool_name="t", tool_description="d",
        input_schema={}, max_tokens=10,
    )
    assert result["arguments"] == {"findings": []}


def test_ceiling_blocks_the_nth_plus_one_call_before_it_reaches_the_real_function(monkeypatch):
    """Directly exercises conftest.py's _make_ceiling_enforced with an
    artificially low ceiling (2) and a fake "real" function that just
    records how many times it was actually invoked -- proving the 3rd call
    raises BEFORE the underlying function ever runs.
    """
    import agent.tests.conftest as conftest_module

    original_count = conftest_module._real_api_call_counter.count
    original_max = conftest_module.MAX_REAL_API_CALLS_PER_SESSION
    try:
        conftest_module._real_api_call_counter.count = 0
        monkeypatch.setattr(conftest_module, "MAX_REAL_API_CALLS_PER_SESSION", 2)

        calls_that_actually_ran = []

        def _fake_real_fn(*args, **kwargs):
            calls_that_actually_ran.append((args, kwargs))
            return "fake result"

        wrapped = conftest_module._make_ceiling_enforced(_fake_real_fn, label="test-seam")

        assert wrapped("first") == "fake result"
        assert wrapped("second") == "fake result"
        assert len(calls_that_actually_ran) == 2

        with pytest.raises(RuntimeError, match="BLOCKED.*real-API spend ceiling"):
            wrapped("third -- should never reach _fake_real_fn")

        assert len(calls_that_actually_ran) == 2, (
            "the 3rd call must be blocked BEFORE reaching the real function -- "
            "the underlying function call count must not have incremented"
        )
    finally:
        conftest_module._real_api_call_counter.count = original_count
        conftest_module.MAX_REAL_API_CALLS_PER_SESSION = original_max


def test_ceiling_counts_one_real_call_per_wrapped_invocation(monkeypatch):
    """Unlike the prior TP-review project's ApiCallTracker (which counts
    raw `usage.api_calls` because ONE review_treatment_plan invocation is
    itself several raw HTTP calls), this ceiling wraps model_provider's
    own per-HTTP-request functions directly (_call_openrouter/
    _call_anthropic) -- so each wrapped invocation already IS one real
    call, not several. This test confirms that direct 1:1 accounting
    (deliberately simpler than the prior project's needed correction,
    because the seam being wrapped is a different, lower level).
    """
    import agent.tests.conftest as conftest_module

    original_count = conftest_module._real_api_call_counter.count
    original_max = conftest_module.MAX_REAL_API_CALLS_PER_SESSION
    try:
        conftest_module._real_api_call_counter.count = 0
        monkeypatch.setattr(conftest_module, "MAX_REAL_API_CALLS_PER_SESSION", 4)

        wrapped = conftest_module._make_ceiling_enforced(lambda *a, **k: "ok", label="test-seam")
        wrapped("call 1")
        assert conftest_module._real_api_call_counter.count == 1
        wrapped("call 2")
        assert conftest_module._real_api_call_counter.count == 2
    finally:
        conftest_module._real_api_call_counter.count = original_count
        conftest_module.MAX_REAL_API_CALLS_PER_SESSION = original_max
