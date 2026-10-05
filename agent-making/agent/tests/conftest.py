"""Structural guardrail — resolves CLAUDE.md's "pending, wire in the moment
either test suite exists" note. Same mechanism as the prior TP-review
project's backend/tests/conftest.py: an autouse fixture patches every real
call site this pipeline has to Anthropic/OpenRouter so it raises BEFORE any
HTTP request is constructed, for every test by default. The one escape
hatch is `@pytest.mark.real_api`, and the marker existing on a test is never
itself permission — a human still approves the exact command, call count,
and cost estimate every time, per CLAUDE.md's hard rule.

Three real call sites exist in this pipeline today, all blocked here:
1. `judge.anthropic.Anthropic` — the class judge.py instantiates directly
   for the real, billed default path (model_override=None).
2. `model_provider._call_openrouter` / `model_provider._call_anthropic` —
   the OpenRouter-default dev/test path (with its own Anthropic fallback).
3. `humanize.humanize_evidence_with_llm` — the optional real-LLM tone
   rewrite pass. Not wired into api.py's review_person_document today
   (only the free, deterministic humanize_evidence is), but blocked
   preemptively so wiring it in later can never silently bypass this
   guardrail by a caller forgetting it needs blocking too.
"""
import os

import pytest


def _blocked(seam_name: str):
    def _raise(*args, **kwargs):
        raise RuntimeError(
            f"BLOCKED by agent-making/agent/tests/conftest.py::_block_real_api_calls: a test "
            f"attempted to call {seam_name}, which would make a real, billed call to the "
            f"Anthropic API (or OpenRouter, which can itself fall back to a real Anthropic call). "
            f"This is blocked for every test in this suite by default. If a test is deliberately "
            f"meant to exercise the real API, mark it explicitly with @pytest.mark.real_api — and "
            f"only run that test with the user's explicit, per-instance approval (exact command + "
            f"call count + cost estimate), per CLAUDE.md's hard rule. Never add that marker to a "
            f"test, or run one that already has it, without that approval already granted for this "
            f"specific run."
        )
    return _raise


MAX_REAL_API_CALLS_PER_SESSION = int(os.environ.get("MAX_REAL_API_CALLS_PER_SESSION", "4"))


class _RealApiCallCounter:
    """Module-level singleton — must survive across every real_api test in
    the session, including each test's own monkeypatch teardown."""

    def __init__(self):
        self.count = 0


_real_api_call_counter = _RealApiCallCounter()


def _make_ceiling_enforced(real_fn, *, label: str):
    """Wraps a REAL call function (only ever reached for a test marked
    @pytest.mark.real_api) so every call counts against one shared
    session-wide ceiling, raising BEFORE calling `real_fn` once the ceiling
    is hit — never a warning logged after the fact.
    """
    def _wrapper(*args, **kwargs):
        if _real_api_call_counter.count >= MAX_REAL_API_CALLS_PER_SESSION:
            raise RuntimeError(
                f"BLOCKED by agent-making's real-API spend ceiling: "
                f"{MAX_REAL_API_CALLS_PER_SESSION} real call(s) already made this pytest session "
                f"(MAX_REAL_API_CALLS_PER_SESSION={MAX_REAL_API_CALLS_PER_SESSION}). Refusing "
                f"another real call ({label}) in this same session, even though this test is "
                f"marked @pytest.mark.real_api — a marker approves THAT test's own calls, not an "
                f"unbounded session total. Raise MAX_REAL_API_CALLS_PER_SESSION explicitly, with "
                f"the user's explicit per-instance approval for the higher count, if more real "
                f"calls are genuinely needed for this run."
            )
        result = real_fn(*args, **kwargs)
        _real_api_call_counter.count += 1
        print(f"[real-api-ceiling] real API calls this session: {_real_api_call_counter.count}/{MAX_REAL_API_CALLS_PER_SESSION} ({label})")
        return result
    return _wrapper


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_api: this test deliberately calls the real Anthropic/OpenRouter API. Requires the "
        "user's explicit, per-instance approval (CLAUDE.md's hard rule) before every run -- never "
        "add or run this marker on your own judgment.",
    )


@pytest.fixture(autouse=True)
def _block_real_api_calls(request, monkeypatch):
    import anthropic

    from agent.pipeline import humanize as humanize_module
    from agent.pipeline import model_provider as model_provider_module

    if request.node.get_closest_marker("real_api") is not None:
        # Fix Round (2026-10-05), "structural gate for the debug-script
        # incident": every real call site now ALSO checks
        # real_api_guard.ensure_real_api_calls_allowed() before it does
        # anything, regardless of whether this pytest-level guardrail
        # exists at all — this is what actually protects a bare script run
        # outside pytest, which has none of this file's fixtures applied.
        # A real_api-marked test is the one place that code-level gate
        # must stand down too, same per-instance approval this marker
        # already requires — set only for this test's own duration.
        monkeypatch.setenv("ALLOW_REAL_API_CALLS", "1")
        monkeypatch.setattr(
            model_provider_module, "_call_openrouter",
            _make_ceiling_enforced(model_provider_module._call_openrouter, label="model_provider._call_openrouter"),
        )
        monkeypatch.setattr(
            model_provider_module, "_call_anthropic",
            _make_ceiling_enforced(model_provider_module._call_anthropic, label="model_provider._call_anthropic"),
        )
        monkeypatch.setattr(
            humanize_module, "humanize_evidence_with_llm",
            _make_ceiling_enforced(humanize_module.humanize_evidence_with_llm, label="humanize.humanize_evidence_with_llm"),
        )
        # judge.py's own direct anthropic.Anthropic() usage is intentionally
        # left real here — a real_api test that exercises THIS seam
        # specifically constructs its own client the same way judge.py
        # does; ceiling-wrapping the class constructor itself isn't a
        # meaningful "count calls" seam the way the two function calls
        # above are.
        yield
        return

    # judge.py does `import anthropic` at module level and calls
    # `anthropic.Anthropic()` directly — patching the shared module's own
    # attribute here blocks that exact seam (judge_module.anthropic IS this
    # same module object, not a copy).
    monkeypatch.setattr(anthropic, "Anthropic", _blocked("judge.anthropic.Anthropic"))
    monkeypatch.setattr(model_provider_module, "_call_openrouter", _blocked("model_provider._call_openrouter"))
    monkeypatch.setattr(model_provider_module, "_call_anthropic", _blocked("model_provider._call_anthropic"))
    monkeypatch.setattr(humanize_module, "humanize_evidence_with_llm", _blocked("humanize.humanize_evidence_with_llm"))
    yield
