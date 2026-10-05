"""Fix Round (2026-10-05): a bare debug script run outside pytest has none
of conftest.py's autouse fixtures applied, so it previously had zero
protection against making a real, billed call -- confirmed, this happened
for real (~$0.43 unintended spend) while debugging a failing test with a
raw script instead of a pytest test. real_api_guard.py is the structural,
code-level fix: every real call site checks it before doing anything,
regardless of whether pytest is even running. These tests exercise the
gate function directly -- no real network access anywhere in this file.
"""
import os

import pytest

from ..pipeline.real_api_guard import RealApiCallsNotAllowed, ensure_real_api_calls_allowed


def test_refuses_by_default_when_env_var_is_unset(monkeypatch):
    monkeypatch.delenv("ALLOW_REAL_API_CALLS", raising=False)
    with pytest.raises(RealApiCallsNotAllowed, match="BLOCKED by real_api_guard"):
        ensure_real_api_calls_allowed("some.seam")


def test_refuses_when_env_var_is_set_to_something_other_than_the_literal_1(monkeypatch):
    # "true"/"yes"/"True" must NOT pass -- only the exact literal "1", so
    # there's one unambiguous way to opt in, not several near-miss spellings
    # that could be set by accident.
    for value in ("true", "True", "yes", "0", ""):
        monkeypatch.setenv("ALLOW_REAL_API_CALLS", value)
        with pytest.raises(RealApiCallsNotAllowed):
            ensure_real_api_calls_allowed("some.seam")


def test_allows_when_env_var_is_exactly_1(monkeypatch):
    monkeypatch.setenv("ALLOW_REAL_API_CALLS", "1")
    ensure_real_api_calls_allowed("some.seam")  # must not raise


def test_error_message_names_the_seam():
    os.environ.pop("ALLOW_REAL_API_CALLS", None)
    with pytest.raises(RealApiCallsNotAllowed, match="my_module.my_function"):
        ensure_real_api_calls_allowed("my_module.my_function")


def test_judge_run_judgment_checks_once_refuses_without_the_env_var():
    """Defense-in-depth proof for the one seam this suite can actually
    exercise the REAL (non-stubbed) function body for: test_model_boundary.
    py's own resolve_judgment_boundary flow temporarily re-binds
    judge_module._run_judgment_checks_once to the true module-level
    function, bypassing conftest's own stub-replacement for that one test.
    See test_model_boundary.py::test_real_path_actually_reaches_the_real_
    call_site_not_the_mock_stub, which asserts this exact gate fires.

    model_provider._call_anthropic/_call_openrouter and humanize.humanize_
    evidence_with_llm are NOT re-tested here the same way: conftest.py's
    own autouse fixture replaces those names ENTIRELY with a blocking stub
    for every non-real_api test (never reaching the real function body at
    all), so a test calling them directly would only prove the stub
    raises, not that THIS gate does — see this file's own module docstring
    for the wiring itself (one line added before each real call site),
    verified by direct code reading rather than a misleading test.
    """
    from ..pipeline import judge as judge_module
    # judge_module._run_judgment_checks_once is never stubbed by conftest's
    # own autouse fixture (only anthropic.Anthropic is, at the shared
    # module level) -- it's the real function here by default, unless some
    # OTHER test in this same session reassigned it directly (e.g.
    # test_model_boundary.py) and didn't clean up; that file's own
    # `_reset_judge_module_state` fixture guarantees it always does.

    os.environ.pop("ALLOW_REAL_API_CALLS", None)
    with pytest.raises(RuntimeError, match="BLOCKED by real_api_guard"):
        judge_module._run_judgment_checks_once(
            judgment_rules=[{"rule_id": "R-1", "description": "test rule"}],
            fields={"pages": []}, rendered_images={},
            tracker=None, call_reason="test", model_override=None,
        )
