"""Priority 1 of a real manual audit round (Round 4 re-verification): for
every not_checkable rule whose evidence is just a developer-facing
cross-reference note ("Same reasoning as SN-97151-17.") -- confirmed
present, verbatim, in 10 of 11 real documents in a real batch -- Haiku was
treating the bare string as an incomplete instruction and responding with
its own first-person refusal/clarification text instead of a rewrite. That
refusal text was then stored and would be shown to a reviewer as if it
were the actual finding. Two independent fixes, tested here:
1. A cross-reference-only input is now skipped before ever reaching the
   model (zero-cost, deterministic, no real call at all).
2. A broadened safety net catches a refusal-shaped response for any OTHER
   degenerate input shape, using the real distinct wordings observed
   across that same real batch (9+ different phrasings for one identical
   input -- this is inherently open-ended, so this is defense-in-depth,
   not the primary fix).
"""
from types import SimpleNamespace

from ..pipeline import humanize as humanize_module
from ..pipeline.humanize import humanize_evidence_with_llm as _real_humanize_evidence_with_llm

# Each of these is a REAL response verbatim from a real production batch,
# all for the identical input "Same reasoning as SN-97151-17." -- these
# are the actual strings Priority 1's broadened safety net must catch.
_REAL_REFUSAL_RESPONSES = [
    "I need the actual text of the finding to rewrite. You've given me a cross-reference (\"Same reasoning as "
    "SN-97151-17\"), but I don't have access to that other finding's text. Please provide the full evidence text "
    "you want me to rewrite, and I'll apply the rules to make it sound like a BCBA explaining it conversationally "
    "to a colleague.",
    "I need the original text of SN-97151-17 to rewrite this compliance finding using the same reasoning. Could "
    "you provide the full evidence text from that checklist item?",
    "I need the original text from SN-97151-17 to rewrite this finding. Could you provide the full evidence text "
    "that this references, or confirm whether you're asking me to rewrite a different compliance item?",
    "I need the original evidence text to rewrite. You've referenced SN-97151-17, but I don't have that document "
    "or the original compliance checklist language you want me to rewrite.",
    "I can't rewrite this without the original evidence text. You've given me only a cross-reference to another "
    "case (SN-97151-17), not the actual compliance finding to rewrite. Please provide the full evidence text that "
    "needs to be rewritten into conversational BCBA tone.",
    "I cannot rewrite this text because it contains no compliance facts, dates, numbers, names, page references, "
    "or substantive content to preserve. \"Same reasoning as SN-97151-17\" is a cross-reference with no evidence "
    "or finding stated.",
    "Unable to process this request. The input references an external case (SN-97151-17) without providing the "
    "actual evidence text that needs rewriting. Please supply the full compliance checklist evidence text you'd "
    "like rewritten in the conversational BCBA style.",
    "I appreciate the instruction, but I can't complete this rewrite without the actual evidence text. You've "
    "given me a reference code (SN-97151-17) but no text to rewrite.",
    "I don't have access to the document or checklist item \"SN-97151-17\" that you're referring to. To rewrite "
    "the evidence text in a BCBA colleague style while preserving all facts and page references, I'd need you to "
    "provide the actual text from that checklist item.",
]


def test_cross_reference_only_evidence_never_makes_a_real_call():
    """The primary fix: zero-cost, deterministic, skipped entirely before
    reaching the model. Passing an object that would blow up if called
    confirms no real call is even attempted."""
    def _client_that_must_not_be_used():
        raise AssertionError("a real call must never be attempted for a cross-reference-only input")

    humanized, usage = _real_humanize_evidence_with_llm(
        "Same reasoning as SN-97151-17.", client=_client_that_must_not_be_used,
    )
    assert humanized == usage["pre_humanize_text"]
    assert usage["input_tokens"] == 0 and usage["cost_usd"] == 0.0


def test_cross_reference_only_matches_real_variants():
    """The real rule_id format varies (SN-97151-17, SN-97153-18, etc.) --
    confirms the pattern isn't accidentally anchored to one specific id."""
    for rule_id in ("SN-97151-17", "SN-97153-18", "SN-97155-05"):
        assert humanize_module._CROSS_REFERENCE_ONLY_RE.match(f"Same reasoning as {rule_id}.")
        assert humanize_module._CROSS_REFERENCE_ONLY_RE.match(f"Same reasoning as {rule_id}")  # no trailing period


def test_broadened_refusal_guard_catches_every_real_observed_wording(monkeypatch):
    """Defense-in-depth: even if a cross-reference-only input somehow still
    reached the model (a prompt change, a different degenerate shape not
    anticipated), every one of the real, distinct refusal wordings actually
    observed in a real production batch must be caught and rejected, never
    stored as if it were a real rewrite."""
    class _FakeUsage:
        def __init__(self):
            self.input_tokens = 50
            self.output_tokens = 60

    for real_refusal_text in _REAL_REFUSAL_RESPONSES:
        class _FakeTextBlock:
            type = "text"
            text = real_refusal_text

        class _FakeResponse:
            content = [_FakeTextBlock()]
            usage = _FakeUsage()
            stop_reason = "end_turn"

        class _FakeClient:
            messages = SimpleNamespace(create=lambda **k: _FakeResponse())

        # A real, substantive finding (not cross-reference-only, so it
        # actually reaches the model in this test) that happens to get a
        # refusal-shaped response back anyway.
        humanized, usage = _real_humanize_evidence_with_llm(
            "Patient's session ran 2.5 hours with 8 goals addressed across the visit.",
            client=_FakeClient(),
        )
        assert usage["rejection_reason"] == "leaked_prompt_meta_response", (
            f"failed to reject real observed refusal text: {real_refusal_text[:60]!r}..."
        )
        # Fell back to the deterministic-only cleaned text, never the
        # refusal text itself.
        assert "I need" not in humanized and "I can" not in humanized and "Unable to" not in humanized


def test_humanize_findings_batch_logs_a_rejection_distinctly_from_a_failure(monkeypatch, capsys):
    """Priority 3's own ask: a rejection (no exception, just a fallback)
    must be logged distinctly from an outright exception, so the two
    cases stay tellable apart in production logs."""
    def _fake_rejected(text, *, client=None, tracker=None):
        return text, {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001, "pre_humanize_text": text,
                      "rejected_missing_page_ref": True, "rejection_reason": "truncated"}

    monkeypatch.setattr(humanize_module, "humanize_evidence_with_llm", _fake_rejected)
    results = humanize_module.humanize_findings_batch(["Some real finding text."], labels=["R-1"])
    assert len(results) == 1
    raw, humanized, usage = results[0]
    assert humanized == raw  # fell back to the raw/cleaned text
    assert usage["rejection_reason"] == "truncated"
    captured = capsys.readouterr()
    assert "REJECTED" in captured.out
    assert "truncated" in captured.out
