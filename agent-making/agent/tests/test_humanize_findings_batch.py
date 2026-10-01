"""The post-review humanization pass (Priority 2 of a real manual audit
round): replaces the old client-side string-truncation heuristic with a
real batch of Haiku calls, run AFTER a document's full review already has
its own raw reasoning text for every rule.

Exercises humanize_findings_batch's own orchestration (ordering, per-item
failure isolation, the max_calls cap) by directly replacing
humanize_evidence_with_llm itself with a controlled fake -- the SAME seam
agent-making's own autouse real-API guardrail (conftest.py) patches by
default, so these tests stand it down deliberately and supply their own
zero-cost fake instead of ever reaching the real network.

CRITICAL: every fake here returns humanize_evidence_with_llm's OWN real
2-tuple shape (humanized_text, usage) -- NOT humanize_findings_batch's own
3-tuple (raw_text, humanized_text, usage) output shape. A real production
crash (confirmed via a real traceback: "ValueError: not enough values to
unpack (expected 3, got 2)" on call #22 of a real batch) was root-caused
to exactly this distinction being blurred: _process_one's success path
used to return humanize_evidence_with_llm's 2-tuple DIRECTLY with no
reshaping, and this test file's OWN earlier fakes made the same mistake in
the opposite direction (returning 3-tuples), which is why they didn't
catch the real bug. Faking the wrong shape here would hide the exact
class of bug this file exists to catch.
"""
import time

from ..pipeline import humanize as humanize_module
from ..pipeline.humanize import humanize_evidence_with_llm as _real_humanize_evidence_with_llm


def test_humanize_findings_batch_returns_results_in_the_same_order(monkeypatch):
    def _fake(text, *, client=None, tracker=None):
        if tracker is not None:
            tracker.check_before_call()
            tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5", usage={"input_tokens": 10, "output_tokens": 5})
        return f"short: {text[:5]}", {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "pre_humanize_text": text}

    monkeypatch.setattr(humanize_module, "humanize_evidence_with_llm", _fake)
    texts = ["Alpha finding text.", "Beta finding text.", "Gamma finding text."]
    results = humanize_module.humanize_findings_batch(texts, labels=["R-1", "R-2", "R-3"])
    assert len(results) == 3
    for i, (raw, humanized, usage) in enumerate(results):
        assert raw == texts[i]
        assert humanized == f"short: {texts[i][:5]}"
        assert usage["cost_usd"] == 0.001


def test_humanize_findings_batch_isolates_one_findings_failure(monkeypatch):
    """Real bug the reference project's own humanize_findings was built to
    fix: one bad model response must never discard the whole batch's
    already-succeeded results."""
    def _fake(text, *, client=None, tracker=None):
        if "Second" in text:
            raise RuntimeError("simulated model failure")
        if tracker is not None:
            tracker.check_before_call()
            tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5", usage={"input_tokens": 10, "output_tokens": 5})
        return f"short: {text}", {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "pre_humanize_text": text}

    monkeypatch.setattr(humanize_module, "humanize_evidence_with_llm", _fake)
    texts = ["First real finding.", "Second real finding (will fail).", "Third real finding."]
    results = humanize_module.humanize_findings_batch(texts, labels=["R-1", "R-2", "R-3"])
    assert len(results) == 3
    raw1, humanized1, usage1 = results[1]
    assert humanized1 == raw1 == texts[1]  # fell back to raw text, unchanged
    assert usage1 == {}
    for i in (0, 2):
        raw, humanized, usage = results[i]
        assert humanized == f"short: {texts[i]}"  # the other two kept their own real result


def test_humanize_findings_batch_respects_max_calls_cap(monkeypatch):
    call_count = {"n": 0}

    def _fake(text, *, client=None, tracker=None):
        if tracker is not None:
            tracker.check_before_call()  # raises ModelCallError once the cap is hit
        call_count["n"] += 1
        if tracker is not None:
            tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5", usage={"input_tokens": 10, "output_tokens": 5})
        return f"short: {text}", {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "pre_humanize_text": text}

    monkeypatch.setattr(humanize_module, "humanize_evidence_with_llm", _fake)
    texts = [f"Finding number {i}." for i in range(5)]
    results = humanize_module.humanize_findings_batch(texts, max_calls=2)
    assert len(results) == 5
    assert call_count["n"] <= 2
    fallbacks = [r for r in results if r[2] == {}]
    assert len(fallbacks) >= 3  # the calls that never got under the cap


def test_humanize_findings_batch_runs_calls_in_real_parallel_not_sequentially(monkeypatch):
    """Urgent production ask (#4, after a real review-time regression):
    a basic timing benchmark so "the concurrency silently became
    sequential" can never ship unnoticed again. Each mocked call sleeps
    0.2s (standing in for real network latency) -- 8 findings run
    sequentially would take >= 1.6s; run genuinely in parallel (8 workers)
    they should all finish close to 0.2s. Asserts well under half the
    sequential time, generous enough to never flake on a slow CI box
    while still catching a real "accidentally sequential" regression."""
    def _fake(text, *, client=None, tracker=None):
        time.sleep(0.2)
        if tracker is not None:
            tracker.check_before_call()
            tracker.record(reason="test", provider="anthropic-fallback", model="claude-haiku-4-5", usage={"input_tokens": 10, "output_tokens": 5})
        return f"short: {text}", {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "pre_humanize_text": text}

    monkeypatch.setattr(humanize_module, "humanize_evidence_with_llm", _fake)
    texts = [f"Finding number {i} with real content." for i in range(8)]

    start = time.monotonic()
    results = humanize_module.humanize_findings_batch(texts, labels=[f"R-{i}" for i in range(8)])
    elapsed = time.monotonic() - start

    assert len(results) == 8
    sequential_time = 0.2 * 8
    assert elapsed < sequential_time / 2, (
        f"took {elapsed:.2f}s for 8 calls at 0.2s each -- sequential execution would take "
        f"~{sequential_time:.2f}s; real parallelism should land close to ~0.2s, not add up"
    )


def test_humanize_findings_batch_empty_list_returns_empty_list():
    assert humanize_module.humanize_findings_batch([]) == []


def test_humanize_evidence_with_llm_never_makes_a_real_call_for_empty_text():
    """humanize_evidence_with_llm's own early-return — captured as a
    direct reference at module-IMPORT time (before the autouse guardrail's
    per-test monkeypatch replaces agent.pipeline.humanize's own module
    attribute), so this exercises the REAL function's own logic, not the
    guardrail's blanket stand-in. Passing an object that would blow up if
    actually called confirms no real call is even attempted."""
    def _client_that_must_not_be_used():
        raise AssertionError("a real call must never be attempted for empty/whitespace-only text")

    humanized, usage = _real_humanize_evidence_with_llm("   ", client=_client_that_must_not_be_used)
    assert humanized == usage["pre_humanize_text"]
    assert usage == {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "pre_humanize_text": humanized}


def test_humanize_findings_batch_reshapes_a_successful_2_tuple_into_the_promised_3_tuple(monkeypatch):
    """The exact real bug, reproduced directly: humanize_evidence_with_llm
    returns a 2-tuple (humanized_text, usage) on success -- confirmed via
    a real production traceback ("ValueError: not enough values to unpack
    (expected 3, got 2)") that a SUCCESSFUL call (not the failure
    fallback) was the one producing the wrong shape. This fake returns
    EXACTLY what the real function returns on success (a bare 2-tuple, no
    raw text alongside it) to prove humanize_findings_batch reshapes it
    into (raw_text, humanized_text, usage) using usage["pre_humanize_text"]
    as the raw value, not just passing the 2-tuple through.
    """
    def _fake_returns_a_bare_2_tuple(text, *, client=None, tracker=None):
        return f"HUMANIZED[{text}]", {"input_tokens": 5, "output_tokens": 3, "cost_usd": 0.0005, "pre_humanize_text": text}

    monkeypatch.setattr(humanize_module, "humanize_evidence_with_llm", _fake_returns_a_bare_2_tuple)
    results = humanize_module.humanize_findings_batch(["Real raw finding text."], labels=["R-1"])
    assert len(results) == 1
    raw, humanized, usage = results[0]  # must not raise ValueError unpacking
    assert raw == "Real raw finding text."
    assert humanized == "HUMANIZED[Real raw finding text.]"
    assert usage["cost_usd"] == 0.0005
