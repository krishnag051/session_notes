"""Fix Round (2026-10-05), "humanizer writing quality": Krishna compared our
humanized output against a reference compliance platform side by side and
found ours reads hedged/padded by comparison -- specifically flagging a
recurring "...though a specific page couldn't be confirmed, but the result is
accurate"-shaped trailing disclaimer. The actual root cause of that exact
wording was upstream (integrity.py's PAGE_UNAVAILABLE_NOTE, see
test_integrity.py), not this prompt -- but this prompt's own rule 1 ("never
drop a real fact") is why it survived humanization unchanged once it was
there, and the same reflexive-hedging shape could originate from other raw
evidence too. This file only confirms the PROMPT TEXT itself carries the new,
explicit anti-hedge instruction (rule 9) and the worked example pinned to the
exact phrase Krishna flagged -- not model behavior, which requires a real,
billed call to verify and is out of scope for this mocked-boundary suite.
"""
from ..pipeline.humanize import _REWRITE_SYSTEM_PROMPT


def test_prompt_explicitly_instructs_cutting_process_hedging():
    assert "reflexive hedging" in _REWRITE_SYSTEM_PROMPT
    assert "process commentary" in _REWRITE_SYSTEM_PROMPT


def test_prompt_worked_example_uses_the_exact_flagged_phrase():
    # The literal phrase from Krishna's real side-by-side comparison --
    # confirms the worked example is anchored to the actual reported
    # symptom, not a generic hedging example that might miss the real shape.
    assert "though a specific page couldn't be confirmed, but the result is accurate" in _REWRITE_SYSTEM_PROMPT


def test_prompt_still_requires_keeping_every_real_fact():
    # Rule 9 must narrow, never override, rule 1 -- a real compliance fact
    # (date/number/name/page) is never "process commentary" and must still
    # survive. Guards against a future edit accidentally widening rule 9
    # into "cut anything that sounds like a caveat."
    assert "Never drop, round, or change a fact" in _REWRITE_SYSTEM_PROMPT
