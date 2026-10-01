"""Three real bugs found via a real manual CSV/PDF audit of a 10-document
batch (names redacted in these tests — synthetic text reproducing the
exact same real structural shapes, never committing real PHI to fixtures):

1. A multi-page note reprints the same patient-identifying header block at
   the top of every page after the first. When "Session Summary" happens
   to land right at a page boundary, the old narrative-extraction regex
   stopped at that REPRINTED header (mistaking it for the real section
   end), producing an empty/near-empty narrative even though the real
   narrative and Session Duration are both genuinely present and readable
   (SN-97153-15's "could not find both" uncertain, confirmed root cause).
2. The nap/sleep/sickness keyword check scanned effectively the whole
   document, false-positiving on illustrative goal-description text (e.g.
   "...where is the baby sleeping...") that is never a real account of
   what happened in session.
3. The sentence-count rule's remark was a bare number with no way to
   verify it.

All exercised directly against fields.py, zero model calls.
"""
from ..pipeline import fields as fields_module

# Mirrors the real template shape exactly (patient-identifying header
# reprinted at the top of every page after the first, ending in a
# "Page N of M" marker) with a fake name/DOB/IDs throughout.
_PAGE_BREAK_HEADER = (
    "Patient Name: Jane Doe  Patient DOB: 01/01/2015 Date of Service: 09/24/2026  "
    "Service Code: Adaptive Behavior Treatment by Protocol Insurance: Test Payer -\n"
    "Medicaid  Insurance ID: TEST1234X \n \nPage 3 of 4 I - Page 3 of 4\n"
)


def _doc_with_narrative_split_by_page_break(narrative_after_break: str) -> str:
    """Session Summary lands with ZERO real content before the page-break
    reprint — the exact real, confirmed shape that produced an empty
    captured narrative with the old regex."""
    return (
        "Goals \n"
        "Some Provider added a data point 50.00 to Some Goal Text for 09/24/2026.\n"
        "Session Summary\n"
        + _PAGE_BREAK_HEADER
        + narrative_after_break
        + "\nProvider Name: Some Provider  Provider Credentials: BT \n"
    )


def test_narrative_section_text_spans_a_page_break_header_reprint():
    text = _doc_with_narrative_split_by_page_break(
        "Patient was excited to see me at the start of the session. "
        "Patient engaged with session for the length of the session."
    )
    fields = {"full_text": text}
    narrative = fields_module._narrative_section_text(fields)
    assert narrative is not None
    assert "excited to see me" in narrative
    assert "engaged with session" in narrative
    assert "Insurance ID" not in narrative  # the reprinted header itself must be gone


def test_sn_97153_15_no_longer_falls_back_to_uncertain_across_a_page_break():
    """The real confirmed bug: Session Duration and narrative both present
    and readable, but the old extraction returned "Could not find both"."""
    text = (
        "Session Duration: 2:00 \n"
        + _doc_with_narrative_split_by_page_break(
            "Patient was excited to see me at the start of the session. "
            "We worked on several goals today. "
            "Patient engaged with session for the length of the session. "
            "Patient responded well to the ABA techniques used during this session."
        )
    )
    fields = {"full_text": text}
    result = fields_module._check_SN_97153_15(fields)
    assert result["result"] in ("pass", "fail")  # a REAL verdict, not "uncertain"
    assert "Could not find" not in result["evidence"]


def test_sn_97153_19_passes_when_sleeping_only_appears_in_a_goal_citation():
    """Real bug: a quoted goal-description citation inside the Session
    Summary itself ("...client will discriminate between a 'who/what/
    where' question..., where is the baby sleeping...") used to false-
    positive this nap/sleep/sickness check."""
    narrative = (
        'Some of the goals worked on today were:\n'
        '"To increase intraverbal skills, client will discriminate between a '
        '“who/what/where” question in a story or natural environment, and respond '
        'correctly. For example, where is the baby sleeping, who is sleeping in the crib '
        'etc.", patient required modeling with this goal. '
        'Patient engaged with session for the length of the session. '
        'Patient responded well to the ABA techniques used during this session.'
    )
    text = _doc_with_narrative_split_by_page_break(narrative)
    fields = {"full_text": text}
    result = fields_module._check_SN_97153_19(fields)
    assert result["result"] == "pass"


def test_sn_97153_19_still_fails_on_a_real_narrative_mention():
    """Regression guard: a GENUINE narrative mention (not inside a goal
    citation) must still fail — the fix narrows scope, it doesn't disable
    the check."""
    narrative = "Patient appeared sleepy and was napping for part of the session."
    text = _doc_with_narrative_split_by_page_break(narrative)
    fields = {"full_text": text, "pages": []}
    result = fields_module._check_SN_97153_19(fields)
    assert result["result"] == "fail"


def test_sentence_count_excerpt_quotes_counted_sentences_when_few():
    narrative = "Patient was excited to see me. Patient engaged well. Session ended on time."
    excerpt = fields_module._sentence_count_excerpt(narrative)
    assert "Patient was excited to see me" in excerpt
    assert "Session ended on time" in excerpt


def test_sentence_count_excerpt_shows_first_and_last_when_many():
    sentences = " ".join(f"Sentence number {i}." for i in range(1, 10))
    excerpt = fields_module._sentence_count_excerpt(sentences)
    assert "Sentence number 1" in excerpt
    assert "Sentence number 9" in excerpt
    assert "Sentence number 5" not in excerpt  # not every sentence, just first/last


def test_sn_97153_15_remark_includes_a_verifiable_excerpt():
    text = (
        "Session Duration: 2:00 \n"
        + _doc_with_narrative_split_by_page_break(
            "Patient was excited to see me at the start of the session. "
            "Patient engaged with session for the length of the session."
        )
    )
    fields = {"full_text": text}
    result = fields_module._check_SN_97153_15(fields)
    assert "excited to see me" in result["evidence"]
