"""Round 5 re-verification: the SAME refusal/meta-acknowledgment failure
shape (Round 4's own test file covers SN-97153-18's "cross-reference-only"
trigger) resurfaced on a DIFFERENT rule -- SN-97153-01/SN-97151-01, a real
judgment rule whose raw_answer lists several quoted proper names in one
finding. Likely trigger: the Round 4 Priority 2 prompt rule ("never merge
two distinct quoted names"), combined with a name-dense input, pushed
Haiku toward acknowledging the instruction instead of silently applying
it. Two changes here:
1. That prompt rule was reworded from an imperative numbered rule into an
   inline worked example (good vs. bad output), per the hypothesis that a
   meta-instruction Haiku might "confirm receipt of" resists this failure
   mode worse than a content example does. This can't be proven fixed
   without a real model call (not run here, per the standing no-real-API-
   without-explicit-approval rule) -- this test file only proves the
   REGRESSION-GUARD side: the broadened, rule-agnostic refusal detector
   below catches this round's own real responses regardless of whether
   the prompt reword eliminates the behavior entirely.
2. The refusal detector itself is now explicitly RULE-AGNOSTIC (checked
   on the response text alone, never tied to a specific rule_id) --
   this exact failure shape has now hit two unrelated rules in two
   rounds, so detection must generalize, not special-case per rule.
"""
from ..pipeline import humanize as humanize_module
from ..pipeline.humanize import humanize_evidence_with_llm as _real_humanize_evidence_with_llm

# Each of these is a REAL response verbatim from a real production batch
# (Round 5), all for SN-97153-01/SN-97151-01 ("are no names other than the
# patient's legal name or AKA used?") -- a real judgment finding listing
# several quoted proper names, NOT a cross-reference-only input (Round 4's
# own trigger). These are the actual strings the broadened, rule-agnostic
# safety net must catch.
_REAL_REFUSAL_RESPONSES_ROUND_5 = [
    "I understand. I'm ready to rewrite compliance checklist evidence text in a conversational BCBA tone while: "
    "- Preserving every fact, date, number, name, and page reference - Keeping it SHORT (same word count or "
    "shorter) - Using minimal punctuation - Including the actual reason/conclusion - Treating PAGEREF tokens as "
    "literal, unbreakable strings - Keeping distinct quoted names properly separated by role Waiting for the "
    "compliance checklist text to rewrite.",
    "I understand. I'm ready to rewrite compliance checklist evidence text in a conversational BCBA tone, "
    "preserving every fact while keeping it short and natural-sounding. Send the text to rewrite.",
    "I understand. I'm ready to rewrite compliance checklist evidence text in a conversational BCBA-to-colleague "
    "style, preserving every fact, treating PAGEREF tokens as literal strings, and keeping distinct quoted names "
    "properly separated by their own role. Please provide the text to rewrite.",
    "I'm ready. Please provide the compliance checklist's evidence text that needs rewriting.",
    "I understand. I'm ready to rewrite compliance checklist evidence text in a natural BCBA colleague-to-colleague "
    "tone, preserving every fact and keeping distinct quoted names attached to their own original role. I'm ready "
    "for the text to rewrite.",
    "I'm ready. Please provide the compliance checklist text that needs rewriting.",
    "I'm ready. Please provide the compliance checklist's evidence text that needs rewriting, and I'll apply all "
    "the rules—preserving every fact, keeping it short, and keeping distinct names properly separated.",
    "I understand. Joseph Bergstein is the patient's legal name, Yossie is their stated AKA, and I'll keep all "
    "other names properly attached to their own role rather than merging them. Please provide the text to "
    "rewrite.",
]


def test_round5_refusal_responses_are_all_caught_by_the_rule_agnostic_guard():
    """Confirms the broadened detector (now rule-agnostic, not tied to
    SN-97153-18's own cross-reference shape) catches every real Round 5
    response, even though these are for a COMPLETELY DIFFERENT rule
    (SN-97153-01) with real, substantive, name-dense input -- not the
    empty/cross-reference-only shape Round 4's own guard targeted."""
    for real_refusal_text in _REAL_REFUSAL_RESPONSES_ROUND_5:

        class _FakeUsage:
            input_tokens = 60
            output_tokens = 70

        class _FakeTextBlock:
            type = "text"
            text = real_refusal_text

        class _FakeResponse:
            content = [_FakeTextBlock()]
            usage = _FakeUsage()
            stop_reason = "end_turn"

        class _FakeClient:
            class messages:
                @staticmethod
                def create(**k):
                    return _FakeResponse()

        humanized, usage = _real_humanize_evidence_with_llm(
            "Only patient name 'Jane Doe' and AKA 'Janie' appear. Provider 'Pat Smith' and BCBA 'Robin Lee' are "
            "labeled as providers, not treated as patient aliases.",
            client=_FakeClient(),
        )
        assert usage["rejection_reason"] == "leaked_prompt_meta_response", (
            f"failed to reject a real Round 5 refusal response: {real_refusal_text[:60]!r}..."
        )
        assert "I understand" not in humanized and "I'm ready" not in humanized and "Please provide" not in humanized


def test_the_reworded_name_separation_rule_is_an_example_not_an_imperative_instruction():
    """Guards against silently reverting the Round 5 fix back to an
    imperative numbered rule (the hypothesized trigger) -- the name-
    separation guidance must live inside a worked good/bad example, not
    as a bare directive Haiku might acknowledge receipt of."""
    prompt = humanize_module._REWRITE_SYSTEM_PROMPT
    assert "Good output" in prompt and "Bad output" in prompt
    assert "AKA" in prompt and "BCBA" in prompt
    # The OLD imperative phrasing from Round 4 must not have crept back in.
    assert "NEVER merge two different quoted names into one phrase" not in prompt
