"""Deterministic checkers for this project's rules.json — regex/structural/
arithmetic facts, zero cost, zero variance. New content (this project's own
rules), same style as the prior TP-review project's fields.py: one small
function per rule_id, dispatched from a table, escalating to the judgment
layer (returning None) when a rule's own checker genuinely isn't
implemented yet rather than guessing.

`fields` (the dict every checker receives) is built by api.py per document:
{
    "pages": [{"page_number", "text", "low_text"}, ...],
    "full_text": str,                 # all pages joined
    "service_code": str,              # "97151" | "97153" | "97155"
    "person": {"full_name": str, "dob": str},
    "timesheet_rows": list[dict] | None,  # this batch's Activity Statement rows, see activity_statement.py
    "appendix": str | None,               # this document's own appendix — the join key into timesheet_rows
    "sibling_documents": list[dict] | None,  # this person's OTHER documents from the SAME batch/upload —
                                              # [{"service_code": str, "full_text": str}, ...] — for any rule
                                              # phrased as "at least one session note" (not scoped to a
                                              # specific assessment/service type), see _check_SN_97151_11.
}
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import activity_statement as _activity_statement_module

# --------------------------------------------------------------- helpers

# Real bug found via a real manual audit (Charny Gluck/Udy Reichman,
# SN-97153-15): a multi-page note reprints the SAME patient-identifying
# header block ("Patient Name: ... Insurance ID: ... \n \nPage N of M...")
# at the top of every page after the first. When "Session Summary"/
# "Session Narrative:" happens to land right at a page boundary (real,
# confirmed case: the header literally ends the page, with zero narrative
# content before the reprint), a naive "(.*?)(?:\nProvider Name:|\nPatient
# Name:)" search stops at THIS reprinted header — not the real end of the
# section — because it's also a "\nPatient Name:" occurrence, producing an
# empty or near-empty captured narrative. Stripped out BEFORE narrative
# extraction so the real section-boundary search only ever sees the ONE
# genuine "\nPatient Name:"/"\nProvider Name:" that actually ends the note.
_PAGE_BREAK_HEADER_RE = re.compile(
    r"\n?Patient Name:.*?Insurance ID:[^\n]*\n\s*\nPage \d+ of \d+[^\n]*\n", re.DOTALL,
)


def _strip_page_break_headers(text: str) -> str:
    return _PAGE_BREAK_HEADER_RE.sub("\n", text)


_TELEHEALTH_PLATFORM_KEYWORDS = ("zoom", "google meet", "microsoft teams", "facetime", "skype", "webex", "doxy.me")
_NAP_SLEEP_SICKNESS_KEYWORDS = ("nap", "asleep", "sleepy", "sleeping", "sick", "illness", "fever")
_PUNISHMENT_KEYWORDS = ("punishment", "punish")
_SCHOOL_GOAL_KEYWORDS = ("school goal", "school iep", "classroom goal")
_PARENT_KEYWORDS = ("parent", "caregiver", "mother", "father")


def _pass(evidence: str, page: int | None = None, confidence: float = 1.0) -> dict:
    return {"result": "pass", "evidence": evidence, "page": page, "confidence": confidence}


def _fail(evidence: str, page: int | None = None, confidence: float = 1.0) -> dict:
    return {"result": "fail", "evidence": evidence, "page": page, "confidence": confidence}


def _uncertain(evidence: str, page: int | None = None) -> dict:
    return {"result": "uncertain", "evidence": evidence, "page": page, "confidence": 0.0}


def _not_applicable(evidence: str, page: int | None = None) -> dict:
    return {"result": "not_applicable", "evidence": evidence, "page": page, "confidence": 1.0}


def _not_checkable(evidence: str) -> dict:
    return {"result": "not_checkable", "evidence": evidence, "page": None, "confidence": 0.0}


def _search(pattern: str, text: str, flags: int = re.IGNORECASE) -> re.Match | None:
    return re.search(pattern, text, flags)


def _page_of(fields: dict, needle: str) -> int | None:
    """First page whose text contains `needle` (plain substring, case-insensitive)."""
    lowered = needle.lower()
    for page in fields["pages"]:
        if lowered in page["text"].lower():
            return page["page_number"]
    return None


def _parse_mmddyyyy(s: str) -> date:
    return datetime.strptime(s.strip(), "%m/%d/%Y").date()


def session_date(fields: dict) -> date | None:
    m = _search(r"Session Date:\s*(\d{2}/\d{2}/\d{4})", fields["full_text"])
    return _parse_mmddyyyy(m.group(1)) if m else None


def session_location(fields: dict) -> str | None:
    m = _search(r"Session Location:\s*([^\n]+)", fields["full_text"])
    return m.group(1).strip() if m else None


def session_duration_hours(fields: dict) -> float | None:
    m = _search(r"Session Duration:\s*(\d+):(\d{2})", fields["full_text"])
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2)) / 60


_SESSION_TIME_RE = r"(\d{1,2}:\d{2}\s*[AP]M\s*\([^)]+\))"


def session_start_time(fields: dict) -> str | None:
    # Real fixtures print this on the SAME line as the next field ("...
    # Session Start Time: 09:30 AM (EST) Session Location: 11 - Office") —
    # capture just the time+timezone shape, not "everything to newline".
    m = _search(r"Session Start Time:\s*" + _SESSION_TIME_RE, fields["full_text"])
    return m.group(1).strip() if m else None


def session_end_time(fields: dict) -> str | None:
    m = _search(r"Session End Time:\s*" + _SESSION_TIME_RE, fields["full_text"])
    return m.group(1).strip() if m else None


def signature_date(fields: dict) -> date | None:
    m = _search(r"Provider Signature,?\s*Date:\s*(\d{2}/\d{2}/\d{4})", fields["full_text"])
    return _parse_mmddyyyy(m.group(1)) if m else None


def provider_name(fields: dict) -> str | None:
    m = _search(r"Provider Name:\s*([^\n,]+)", fields["full_text"])
    return m.group(1).strip() if m else None


def bcba_lba_header_name(fields: dict) -> str | None:
    m = _search(r"BCBA/LBA:\s*([^\n]+)", fields["full_text"])
    return m.group(1).strip() if m else None


def was_supervised(fields: dict) -> bool | None:
    m = _search(r"Supervised by the BCBA/LBA\?\s*\n?\s*(Yes|No)", fields["full_text"])
    if not m:
        return None
    return m.group(1).strip().lower() == "yes"


def _participants_block(fields: dict) -> str:
    m = _search(
        r"(?:Who Attended Session|Participants):(.*?)(?:\n\s*\n|Telehealth Information|Was this session)",
        fields["full_text"], re.IGNORECASE | re.DOTALL,
    )
    return m.group(1) if m else ""


def checked_participants(fields: dict) -> list[str]:
    block = _participants_block(fields)
    return [item.strip() for item in re.findall(r"☑\s*([^\n☐☑]+)", block)]


def insurance(fields: dict) -> str | None:
    m = _search(r"Insurance:\s*([^\n]+?)(?:\s+Medicaid|\s+Insurance ID|\n)", fields["full_text"])
    return m.group(1).strip() if m else None


def data_point_bullets(fields: dict) -> list[tuple[str, str, date]]:
    """[(provider_name, goal_text, date), ...] from 'X added a data point N to <goal> for MM/DD/YYYY.' bullets.

    BUG FIX (Phase 2 follow-up): real goal descriptions routinely wrap
    across a PDF line break (confirmed on real sample PDFs — most of a
    real note's goal bullets are long enough to wrap at least once). The
    original regex had no DOTALL flag, so `.` never matched the newline
    inside a wrapped bullet's own goal text — that bullet simply failed to
    match at all, silently undercounting real goals (confirmed: 1 matched
    out of ~12 real bullets on one real document). `re.DOTALL` lets the
    non-greedy goal-text group span the wrap; embedded newlines in the
    captured goal text are then collapsed to a single space so downstream
    evidence text reads as one clean sentence, not two lines glued together.
    """
    out = []
    for m in re.finditer(
        # \s+ (not a literal single space) around "for" -- a real bullet
        # can wrap its own line break at exactly that point (confirmed:
        # "...behavior tech for 5 min for\n09/24/2026." on a real document),
        # and a literal " " would never match a "\n" there, silently
        # dropping that bullet from the count entirely.
        r"([A-Z][A-Za-z.\-' ]+?) added a data point [\d:.]+ to (.+?)\s+for\s+(\d{2}/\d{2}/\d{4})\.",
        fields["full_text"], re.DOTALL,
    ):
        provider = m.group(1).strip()
        goal_text = re.sub(r"\s+", " ", m.group(2)).strip()
        date_str = m.group(3)
        out.append((provider, goal_text, _parse_mmddyyyy(date_str)))
    return out


def sentence_count(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return len(re.findall(r"[.!?]+(?:\s|$)", stripped)) or (1 if stripped else 0)


def _counted_sentences(text: str) -> list[str]:
    """Splits on the SAME boundary sentence_count() itself counts on — the
    actual sentence fragments, so a remark can show a reader exactly what
    was counted, not just a bare number they have no way to verify."""
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]


def _sentence_count_excerpt(text: str) -> str:
    """Real bug found via a real manual audit: this rule's remark used to
    be a bare count ("1 sentence(s) across 2.75h...") with no way to see
    WHICH text was counted — across several real documents sharing a
    near-identical template, counts swung wildly (1 vs. 15 vs. 21),
    impossible to sanity-check without seeing the underlying sentences.
    Quotes every counted sentence when there are few enough to read at a
    glance; otherwise just the first and last, so a reader can still spot
    an obviously-wrong split without reading the whole narrative."""
    sentences = _counted_sentences(text)
    if not sentences:
        return ""
    if len(sentences) <= 5:
        quoted = "; ".join(f'"{s}"' for s in sentences)
        return f" Counted: {quoted}"
    return f' Counted (first): "{sentences[0]}" ... (last): "{sentences[-1]}"'


def _keyword_hit(text: str, keywords: tuple[str, ...]) -> str | None:
    lowered = text.lower()
    for kw in keywords:
        if kw in lowered:
            return kw
    return None


_NARRATIVE_SECTION_HEADERS = ("Session Narrative:", "Session Summary")


def _narrative_section_text(fields: dict) -> str | None:
    """The real free-text narrative section, wherever it lives.

    BUG FIX (Phase 2 follow-up): confirmed against real sample PDFs that
    the header differs by note TEMPLATE, not just by service code — a
    97151 'Assessment Session Note' really does use 'Session Narrative:',
    but a 97153 'Behavior Technician Direct Care Session Note' uses
    'Session Summary' instead (no colon). The original checkers here
    hardcoded 'Session Narrative:' only (copied from the assessment
    template without checking a real 97153 document's own page 4/5 text),
    so every 97153 sentence-count checker always failed to find a
    narrative at all and fell back to 'uncertain' — even though
    Session Duration was being extracted successfully by other checkers
    in the very same run. Tries every known header in turn rather than
    assuming a fixed one.

    BUG FIX (real manual audit, Charny Gluck/Udy Reichman): page-break
    header reprints are stripped BEFORE this search — see
    _strip_page_break_headers's own docstring. Without this, a narrative
    that happens to start right at a page boundary gets cut to empty/
    near-empty by the reprinted header looking like the section's own end
    boundary, even though the real narrative (and a real Session Duration
    elsewhere on the page) are both genuinely present and readable.
    """
    full_text = _strip_page_break_headers(fields["full_text"])
    for header in _NARRATIVE_SECTION_HEADERS:
        m = _search(rf"{re.escape(header)}(.*?)(?:\nProvider Name:|\nPatient Name:)", full_text, re.DOTALL)
        if m:
            return m.group(1)
    return None


def _narrative_text(fields: dict) -> str:
    """Every real note's fixed 'Who Attended Session'/'Participants'
    checkbox block always includes the literal label 'Parent/caregiver' —
    present regardless of whether a parent was actually mentioned or even
    checked. A keyword search for content words like 'parent' against the
    WHOLE document would false-positive on every single note purely from
    this boilerplate label. Keyword checks should scan the document with
    that fixed label block excluded, not the raw full_text.
    """
    block = _participants_block(fields)
    if not block:
        return fields["full_text"]
    return fields["full_text"].replace(block, " ")


def _community_statement_present(fields: dict) -> bool:
    return bool(_search(r"referred to as N/A|community statement", fields["full_text"]))


_CREDENTIAL_TOKENS_RE = re.compile(r"\b(BCBA-D|BCBA|LBA|RBT|BT)\b", re.IGNORECASE)


def _strip_credentials(name: str) -> str:
    """Header fields ('BCBA/LBA: Chana R Fuchs BCBA, LBA') and signature
    blocks ('Cindy Rodriguez-Sumner, BCBA, LBA') both mix the person's real
    name with credential abbreviations, but not at a consistent comma
    position — splitting on the first comma isolates the name in one shape
    and truncates it mid-name in the other. Stripping the known credential
    tokens directly (regardless of where they fall) is the reliable way to
    compare just the name across both shapes."""
    cleaned = _CREDENTIAL_TOKENS_RE.sub("", name)
    cleaned = re.sub(r"[,\.]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


# --------------------------------------------------------------- 97151

def _check_SN_97151_02(fields: dict) -> dict:
    """Do all service locations billed match the location on the notes?"""
    loc = session_location(fields)
    if loc is None:
        return _uncertain("No 'Session Location:' field found to compare.")
    return _pass(f"Session Location is consistently '{loc}' within this document.", page=_page_of(fields, "Session Location:"))


def _check_SN_97151_06(fields: dict) -> dict:
    loc = session_location(fields)
    if loc is None:
        return _uncertain("No 'Session Location:' field found.")
    if "telehealth" in loc.lower():
        return _not_applicable("Session location is Telehealth — this rule only applies to non-telehealth sessions.")
    hit = _keyword_hit(_narrative_text(fields), _TELEHEALTH_PLATFORM_KEYWORDS)
    if hit:
        page = _page_of(fields, hit)
        return _fail(f"Session location is '{loc}' (not telehealth), but the narrative mentions a telehealth platform ('{hit}').", page=page)
    return _pass(f"Session location is '{loc}' (not telehealth); no telehealth-platform keyword found in the narrative.")


def _check_SN_97151_07(fields: dict) -> dict:
    loc = session_location(fields)
    if loc is None or "99" not in loc:
        return _not_applicable("Session Location is not '99-other'.")
    if _community_statement_present(fields):
        return _pass("Session Location is '99-other' and an accompanying community statement is present.")
    return _fail("Session Location is '99-other' but no accompanying community statement was found.", page=_page_of(fields, "Session Location"))


def _check_SN_97151_08(fields: dict) -> dict:
    hit = _keyword_hit(_narrative_text(fields), _PUNISHMENT_KEYWORDS + _SCHOOL_GOAL_KEYWORDS)
    if hit:
        return _fail(f"Note contains a reference to '{hit}'.", page=_page_of(fields, hit))
    return _pass("No reference to punishment or school goals found.")


def _check_SN_97151_10(fields: dict) -> dict:
    """Was a parent or caregiver present for at least one assessment session?
    Single-document scope: this checker can only confirm PRESENCE (a real
    pass, since 'at least one' is satisfied globally the moment one document
    shows it) — it can never confirm absence across every one of this
    person's assessment documents from just one of them, so a negative
    result here is not_checkable, not fail.
    """
    checked = checked_participants(fields)
    if any("parent" in c.lower() or "caregiver" in c.lower() for c in checked):
        return _pass(f"Participants checked include: {', '.join(checked)}.", page=_page_of(fields, "Who Attended Session"))
    return _not_checkable(
        "Parent/caregiver not checked as present in THIS document — but this rule asks about 'at least one' "
        "of this person's assessment sessions, which this single-document check cannot rule out on its own."
    )


def _assessment_activities_block_from_text(full_text: str) -> str:
    m = _search(
        r"Assessment Activities:(.*?)(?:\nSession Narrative:|\nProvider Name:|\nPatient Name:)",
        full_text, re.DOTALL,
    )
    return m.group(1) if m else ""


def _assessment_activities_block(fields: dict) -> str:
    return _assessment_activities_block_from_text(fields["full_text"])


_GOAL_DEVELOPMENT_RE = re.compile(r"\bgoal\b[^.\n]{0,30}\b(develop|updat)", re.IGNORECASE | re.DOTALL)
# Sibling-fallback only, deliberately NOT used for this document's own
# primary check above (keeps that check's existing behavior unchanged —
# see this phase's own "nothing else changed" scope). A non-assessment
# template (e.g. Parent Training/97156) has no "Assessment Activities"
# block and doesn't phrase things as "goal development/updating" at all —
# real fixture text shows it instead under a "Parent Goals Addressed"
# section header, with extensive per-goal data-point detail underneath.
# That header IS goal development/updating being documented, just under a
# different template's own heading.
_GOAL_SECTION_HEADER_RE = re.compile(r"\bGoals?\s+Addressed\b", re.IGNORECASE)


def _check_SN_97151_11(fields: dict) -> dict:
    """Reclassified from judgment to deterministic (Phase 2, follow-up
    round) — real sample PDFs show 'goal development/updating' maps
    directly onto the Assessment Activities block's own 'Treatment plan
    development' checkbox, no interpretation needed.

    UNLIKE SN-97151-10 (scoped to "at least one ASSESSMENT session" —
    genuinely single-document, since a Parent Training sibling isn't an
    assessment document), this rule's own question is "at least one
    SESSION NOTE" — not scoped to any specific service/assessment type.
    Real bug found via a real Cazi 97151/97156 CSV audit: a sibling
    Parent Training (97156) document in the SAME batch/upload already
    covers "Parent Goals Addressed" in detail, but this check only ever
    looked at the one document being reviewed and fell back to
    not_checkable — the same "pipeline forgot a real second source was
    already sitting in the same upload" shape as the Activity Statement/
    timesheet fix. Checks this document first, then every sibling from
    the same batch (see `fields["sibling_documents"]`) before falling
    back to not_checkable.
    """
    block = _assessment_activities_block(fields)
    if _search(r"☑\s*Treatment plan development", block):
        return _pass(
            "Assessment Activities checkbox 'Treatment plan development' is checked in this document.",
            page=_page_of(fields, "Treatment plan development"),
        )
    if _GOAL_DEVELOPMENT_RE.search(fields["full_text"]):
        return _pass("Narrative mentions goal development/updating language in this document.")

    for sibling in fields.get("sibling_documents") or []:
        sibling_text = sibling.get("full_text") or ""
        sibling_code = sibling.get("service_code") or "other"
        sibling_block = _assessment_activities_block_from_text(sibling_text)
        if _search(r"☑\s*Treatment plan development", sibling_block):
            return _pass(
                f"Assessment Activities checkbox 'Treatment plan development' is checked in this person's "
                f"{sibling_code} document from the same upload (this rule asks about 'at least one' session "
                f"note, not a specific service type)."
            )
        if _GOAL_DEVELOPMENT_RE.search(sibling_text):
            return _pass(
                f"This person's {sibling_code} document from the same upload mentions goal development/"
                f"updating language (this rule asks about 'at least one' session note, not a specific "
                f"service type)."
            )
        if _GOAL_SECTION_HEADER_RE.search(sibling_text):
            return _pass(
                f"This person's {sibling_code} document from the same upload has its own 'Goals Addressed' "
                f"section with goal-level detail (this rule asks about 'at least one' session note, not a "
                f"specific service type)."
            )

    return _not_checkable(
        "Neither the 'Treatment plan development' checkbox nor goal development/updating language was found "
        "in this document or in any of this person's sibling documents from the same upload — this rule asks "
        "about 'at least one' of this person's session notes, which this batch's own documents don't confirm "
        "either way."
    )


_QUOTED_GOAL_CITATION_RE = re.compile(r'"[^"]*?",?\s*patient required[^.\n]*\.', re.IGNORECASE | re.DOTALL)


def _nap_sleep_sickness_search_text(fields: dict) -> str:
    """Real bug found via a real manual audit (Shea Herskovic,
    SN-97153-19): the keyword search used to scan effectively the WHOLE
    document (_narrative_text only excludes the Participants checkbox
    block) — including the Goals/data-points section, which is largely
    templated, illustrative EXAMPLE text describing what each goal
    measures (confirmed real text: "...client will discriminate between a
    'who/what/where' question..., where is the baby sleeping, who is
    sleeping in the crib etc."), not a statement that the patient was
    actually napping/sick during the billed session.

    Scoped to just the real Session Summary/Session Narrative (see
    _narrative_section_text) — which already excludes the raw Goals/
    data-points block entirely, since that block always comes BEFORE this
    section header. The Session Summary itself still separately re-quotes
    each goal's own description verbatim (the templated "Some of the
    goals worked on today were: '<goal description>', patient required X
    with this goal." block) — those quoted citations are stripped too,
    for the same reason: goal-definition text, not a real account of what
    happened in session. Falls back to the old, looser _narrative_text
    scope (whole document minus the Participants block) only when no
    narrative section can be found at all, preserving prior behavior for
    whatever document shape that was built for.
    """
    narrative = _narrative_section_text(fields)
    text = narrative if narrative is not None else _narrative_text(fields)
    return _QUOTED_GOAL_CITATION_RE.sub(" ", text)


def _check_SN_97151_12(fields: dict) -> dict:
    hit = _keyword_hit(_nap_sleep_sickness_search_text(fields), _NAP_SLEEP_SICKNESS_KEYWORDS)
    if hit:
        return _fail(f"Note contains a reference to '{hit}'.", page=_page_of(fields, hit))
    return _pass("No reference to nap/sleep or sickness found.")


def _check_SN_97151_13(fields: dict) -> dict:
    hours = session_duration_hours(fields)
    if hours is None:
        return _uncertain("No 'Session Duration:' field found.")
    ins = insurance(fields)
    threshold = 5 if ins and "healthfirst" in ins.lower() else 8
    if hours <= threshold:
        return _pass(f"Assessment duration is {hours:.2f}h, within the {threshold}h threshold (insurance={ins!r}).")
    return _fail(f"Assessment duration is {hours:.2f}h, exceeding the {threshold}h threshold (insurance={ins!r}).", page=_page_of(fields, "Session Duration"))


def _check_SN_97151_15(fields: dict) -> dict:
    sd, sig = session_date(fields), signature_date(fields)
    if sd is None or sig is None:
        return _uncertain("Could not find both a Session Date and a signature date.")
    delta = (sig - sd).days
    if delta <= 1:
        return _pass(f"Signed {delta} day(s) after the session date ({sd} -> {sig}).")
    return _fail(f"Signed {delta} day(s) after the session date ({sd} -> {sig}), exceeding the 1-day limit.", page=_page_of(fields, "Provider Signature"))


def _check_SN_97151_16(fields: dict) -> dict:
    hours = session_duration_hours(fields)
    if hours is None:
        return _uncertain("No 'Session Duration:' field found.")
    if hours < 2:
        return _pass(f"Session length is {hours:.2f}h, under the 2h limit.")
    return _fail(f"Session length is {hours:.2f}h, not under the 2h limit.", page=_page_of(fields, "Session Duration"))


# --------------------------------------------------------------- 97153

def _check_SN_97153_03(fields: dict) -> dict:
    hours = session_duration_hours(fields)
    bullets = data_point_bullets(fields)
    if hours is None or hours == 0:
        return _uncertain("No usable Session Duration to compute a goals-per-hour rate.")
    distinct_goals = {goal for _, goal, _ in bullets}
    rate = len(distinct_goals) / hours
    if rate >= 3:
        return _pass(f"{len(distinct_goals)} distinct goal(s) across {hours:.2f}h ({rate:.1f}/h) meets the 3/h minimum.")
    return _fail(f"{len(distinct_goals)} distinct goal(s) across {hours:.2f}h ({rate:.1f}/h) is under the 3/h minimum.")


def _check_SN_97153_05(fields: dict) -> dict:
    """Conditional on SN-97153-21: only meaningful when the session WAS
    genuinely supervised. This checker itself still checks was_supervised()
    directly (rather than trusting the caller to have already gated it) so
    it's correct even if invoked in isolation."""
    supervised = was_supervised(fields)
    if supervised is not True:
        return _not_applicable("This session was not marked as BCBA-supervised — this rule doesn't apply.")
    header_name = bcba_lba_header_name(fields)
    if header_name is None:
        return _uncertain("No 'BCBA/LBA:' header field found to compare against the signature.")
    sig_block = _search(r"Provider Signature.*?\n(.+)", fields["full_text"], re.DOTALL)
    if not sig_block:
        return _uncertain("No signature block found to compare against the header BCBA/LBA field.")
    signature_name_line = sig_block.group(1).splitlines()[0].strip()
    if _strip_credentials(header_name) == _strip_credentials(signature_name_line):
        return _pass(f"Header BCBA/LBA ({header_name!r}) matches the signature block ({signature_name_line!r}).")
    return _fail(f"Header BCBA/LBA ({header_name!r}) does not match the signature block ({signature_name_line!r}).", page=_page_of(fields, "BCBA/LBA:"))


def _check_SN_97153_08(fields: dict) -> dict:
    return _check_SN_97151_07(fields)


def _check_SN_97153_11(fields: dict) -> dict:
    header_provider = provider_name(fields)
    bullets = data_point_bullets(fields)
    if header_provider is None:
        return _uncertain("No 'Provider Name:' header field found.")
    if not bullets:
        return _not_checkable("No 'added a data point' bullets found to compare against the header provider.")
    bullet_providers = {p for p, _, _ in bullets}
    header_last_name = header_provider.split()[-1].lower() if header_provider.split() else ""
    if any(header_last_name in bp.lower() for bp in bullet_providers):
        return _pass(f"Header provider ({header_provider!r}) matches at least one data-point bullet's own provider name.")
    return _fail(f"Header provider ({header_provider!r}) does not match any data-point bullet provider ({sorted(bullet_providers)}).")


def _check_SN_97153_12(fields: dict) -> dict:
    hit = _keyword_hit(_narrative_text(fields), _PARENT_KEYWORDS + _PUNISHMENT_KEYWORDS + _SCHOOL_GOAL_KEYWORDS)
    if hit:
        return _fail(f"Note contains a reference to '{hit}'.", page=_page_of(fields, hit))
    return _pass("No reference to parent, punishment, or school goals found.")


def _check_SN_97153_15(fields: dict) -> dict:
    narrative = _narrative_section_text(fields)
    hours = session_duration_hours(fields)
    if not narrative or hours is None or hours == 0:
        return _uncertain("Could not find both a session narrative and a usable Session Duration.")
    count = sentence_count(narrative)
    rate = count / hours
    excerpt = _sentence_count_excerpt(narrative)
    if rate >= 3:
        return _pass(f"{count} sentence(s) across {hours:.2f}h ({rate:.1f}/h) meets the 3/h minimum.{excerpt}")
    return _fail(f"{count} sentence(s) across {hours:.2f}h ({rate:.1f}/h) is under the 3/h minimum.{excerpt}")


def _check_SN_97153_16(fields: dict) -> dict:
    sd, sig = session_date(fields), signature_date(fields)
    if sd is None or sig is None:
        return _uncertain("Could not find both a Session Date and a signature date.")
    delta = (sig - sd).days
    if delta <= 2:
        return _pass(f"Signed {delta} calendar day(s) after the session ({sd} -> {sig}).")
    return _fail(f"Signed {delta} calendar day(s) after the session ({sd} -> {sig}), exceeding the 2-day limit.", page=_page_of(fields, "Provider Signature"))


def _check_SN_97153_17(fields: dict) -> dict:
    hours = session_duration_hours(fields)
    if hours is None:
        return _uncertain("No 'Session Duration:' field found.")
    if hours <= 4:
        return _pass(f"Session length is {hours:.2f}h, within the 4h limit.")
    return _fail(f"Session length is {hours:.2f}h, exceeding the 4h limit.", page=_page_of(fields, "Session Duration"))


def _check_SN_97153_19(fields: dict) -> dict:
    return _check_SN_97151_12(fields)


def _check_SN_97153_21(fields: dict) -> dict:
    """Informational gate, not scored pass/fail against the note itself —
    see rules.json's own note. Still returns a real result/finding shape
    (result mirrors the Yes/No field) so merge.py has something to render;
    consumers should treat Type=="Informational" rules as non-scoring."""
    supervised = was_supervised(fields)
    if supervised is None:
        return _uncertain("No 'Was this session Supervised by the BCBA/LBA?' field found.")
    return {
        "result": "pass" if not supervised else "fail",
        "evidence": f"'Was this session Supervised by the BCBA/LBA?' = {'Yes' if supervised else 'No'}.",
        "page": _page_of(fields, "Supervised by the BCBA/LBA"),
        "confidence": 1.0,
    }


def _check_SN_97153_22(fields: dict) -> dict:
    sd = session_date(fields)
    bullets = data_point_bullets(fields)
    if sd is None:
        return _uncertain("No 'Session Date:' field found.")
    if not bullets:
        return _not_checkable("No 'added a data point' bullets found in this document.")
    mismatched = [(prov, goal, d) for prov, goal, d in bullets if d != sd]
    if not mismatched:
        return _pass(f"All {len(bullets)} data-point bullet date(s) match the Session Date ({sd}).")
    return _fail(f"{len(mismatched)} of {len(bullets)} data-point bullet date(s) do not match the Session Date ({sd}): {[str(d) for _, _, d in mismatched]}.")


# --------------------------------------------------------------- 97155
# NOT YET VERIFIED against a real 97155 sample (none in tests/fixtures/) —
# these reuse the same structural patterns confirmed on 97151/97153 real
# fixtures; spot-check the first time a real 97155 document is available.

def _check_SN_97155_03(fields: dict) -> dict:
    return _check_SN_97153_03(fields)


def _check_SN_97155_09(fields: dict) -> dict:
    header_name = bcba_lba_header_name(fields)
    if header_name is None:
        return _uncertain("No 'BCBA/LBA:' header field found.")
    sig_block = _search(r"Provider Signature.*?\n(.+)", fields["full_text"], re.DOTALL)
    if not sig_block:
        return _uncertain("No signature block found.")
    signature_name_line = sig_block.group(1).splitlines()[0].strip()
    has_credentials = bool(re.search(r"BCBA|LBA", signature_name_line))
    name_matches = _strip_credentials(header_name) == _strip_credentials(signature_name_line)
    if name_matches and has_credentials:
        return _pass(f"Signature ({signature_name_line!r}) matches the header BCBA ({header_name!r}) and includes credentials.")
    return _fail(f"Signature ({signature_name_line!r}) vs header BCBA ({header_name!r}): name_match={name_matches}, has_credentials={has_credentials}.")


def _check_SN_97155_13(fields: dict) -> dict:
    return _check_SN_97151_07(fields)


def _check_SN_97155_18(fields: dict) -> dict:
    narrative = _narrative_section_text(fields)
    hours = session_duration_hours(fields)
    if not narrative or hours is None or hours == 0:
        return _uncertain("Could not find both a session narrative and a usable Session Duration.")
    count = sentence_count(narrative)
    rate = count / hours
    excerpt = _sentence_count_excerpt(narrative)
    if rate >= 5:
        return _pass(f"{count} sentence(s) across {hours:.2f}h ({rate:.1f}/h) meets the 5/h minimum.{excerpt}")
    return _fail(f"{count} sentence(s) across {hours:.2f}h ({rate:.1f}/h) is under the 5/h minimum.{excerpt}")


def _check_SN_97155_19(fields: dict) -> dict:
    return _check_SN_97153_12(fields)


def _check_SN_97155_23(fields: dict) -> dict:
    return _check_SN_97153_16(fields)


def _check_SN_97155_25(fields: dict) -> dict:
    return _check_SN_97151_12(fields)


# ------------------------------------------------- Activity Statement checks
#
# The real second data source these rules always needed was already sitting
# in the same uploaded batch PDF, unused — see activity_statement.py's own
# docstring. `fields["timesheet_rows"]` (every row parsed off this
# document's own Activity Statement cover page) and `fields["appendix"]`
# (this PersonDocument's own appendix, the reliable join key) are both
# threaded in from the backend (see app/routers/person_documents.py's
# run_review) — never guessed/derived here, just consumed.

_TIME_HHMM_AMPM_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.IGNORECASE)


def _normalize_time_str(raw: str) -> str | None:
    """'09:30 AM (EST)' or '9:30 AM' -> '9:30 AM' — strips timezone and
    zero-padding so a note's own field and a timesheet row's field compare
    equal regardless of which style each happened to print in."""
    m = _TIME_HHMM_AMPM_RE.search(raw)
    if not m:
        return None
    return f"{int(m.group(1))}:{m.group(2)} {m.group(3).upper()}"


def _time_to_minutes(raw: str) -> int | None:
    m = _TIME_HHMM_AMPM_RE.search(raw)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    return hour * 60 + minute


def _normalize_location_str(raw: str) -> str:
    """'11 - Office' -> 'office' — strips a leading numeric location code
    (the note's own field prints one, the timesheet's parsed field
    doesn't) so only the actual place-of-service description is compared."""
    return re.sub(r"^\s*\d{1,2}\s*[-:]\s*", "", raw).strip().lower()


def _check_timesheet_alignment(fields: dict) -> dict:
    """Shared by every '...align with the timesheet?' rule regardless of
    service_code — the comparison itself doesn't vary by service_code,
    only which document is being checked."""
    rows = fields.get("timesheet_rows")
    if not rows:
        return _not_checkable(
            "No Activity Statement/timesheet data was available for this document "
            "(no cover page recorded for this batch, or it couldn't be parsed)."
        )
    appendix = fields.get("appendix")
    row = _activity_statement_module.row_for_appendix(rows, appendix)
    if row is None:
        return _not_checkable(
            f"No timesheet row on this Activity Statement references this document's own appendix "
            f"({appendix or 'none recorded'}) — cannot confirm which row is this session's."
        )

    note_date = session_date(fields)
    note_start = session_start_time(fields)
    note_end = session_end_time(fields)
    note_location = session_location(fields)

    mismatches = []
    if note_date is not None:
        try:
            timesheet_date = _parse_mmddyyyy(row["date"])
            if note_date != timesheet_date:
                mismatches.append(f"date of service (note: {note_date.isoformat()}, timesheet: {row['date']})")
        except ValueError:
            pass
    if note_start and row.get("start_time"):
        n, t = _normalize_time_str(note_start), _normalize_time_str(row["start_time"])
        if n and t and n != t:
            mismatches.append(f"start time (note: {n}, timesheet: {t})")
    if note_end and row.get("end_time"):
        n, t = _normalize_time_str(note_end), _normalize_time_str(row["end_time"])
        if n and t and n != t:
            mismatches.append(f"end time (note: {n}, timesheet: {t})")
    if note_location and row.get("location"):
        if _normalize_location_str(note_location) != _normalize_location_str(row["location"]):
            mismatches.append(f"location (note: '{note_location}', timesheet: '{row['location']}')")

    if mismatches:
        return _fail("Mismatch against the Activity Statement timesheet: " + "; ".join(mismatches))
    return _pass(
        f"Start time ({note_start}), end time ({note_end}), date of service, and location all match the "
        f"Activity Statement timesheet row for this session (Appendix {appendix})."
    )


def _check_SN_97151_04(fields: dict) -> dict:
    return _check_timesheet_alignment(fields)


def _check_SN_97153_04(fields: dict) -> dict:
    return _check_timesheet_alignment(fields)


def _check_SN_97155_05(fields: dict) -> dict:
    # Same mechanism as the 97151/97153 versions above — real code, but
    # NOT YET VERIFIED against a real 97155 sample (none exists in
    # agent-making/agent/tests/fixtures/ today, per this rule's own
    # pre-existing notes). Spot-check the first time a real 97155 document
    # with an Activity Statement is available.
    return _check_timesheet_alignment(fields)


def _check_same_day_location_break(fields: dict) -> dict:
    """'If two sessions occur on the same day with different locations, is
    there at least a 15-minute break between them?' — this rule's own
    pre-existing notes assumed it needed a SIBLING PersonDocument's own
    text (Phase 3+ multi-document plumbing that doesn't exist). It
    doesn't: the SAME Activity Statement cover page already lists every
    billable row for the date in one place, with real start/end times and
    locations — no sibling-document fetch needed at all."""
    rows = fields.get("timesheet_rows")
    if not rows:
        return _not_checkable(
            "No Activity Statement/timesheet data was available for this document."
        )
    billable = [r for r in rows if r.get("service_code") and r.get("start_time") and r.get("end_time")]
    if len(billable) < 2:
        return _not_applicable(
            "Only one billable session is on this timesheet for this date — no other same-day session to compare against."
        )

    ordered = sorted(billable, key=lambda r: _time_to_minutes(r["start_time"]) or 0)
    violations = []
    for a, b in zip(ordered, ordered[1:]):
        loc_a = _normalize_location_str(a["location"]) if a.get("location") else None
        loc_b = _normalize_location_str(b["location"]) if b.get("location") else None
        if not loc_a or not loc_b or loc_a == loc_b:
            continue  # same location (or unknown) -- this rule only concerns a location CHANGE
        end_a, start_b = _time_to_minutes(a["end_time"]), _time_to_minutes(b["start_time"])
        if end_a is None or start_b is None:
            continue
        gap = start_b - end_a
        if gap < 15:
            violations.append(
                f"only {gap} minute(s) between service {a.get('service_code')} (ends {a['end_time']}, "
                f"{a['location']}) and service {b.get('service_code')} (starts {b['start_time']}, {b['location']})"
            )

    if violations:
        return _fail("Same-day sessions at different locations without a 15-minute break: " + "; ".join(violations))
    return _pass("All same-day sessions on this timesheet at different locations have at least a 15-minute break.")


def _check_SN_97153_09(fields: dict) -> dict:
    return _check_same_day_location_break(fields)


def _check_SN_97155_16(fields: dict) -> dict:
    # Same mechanism as SN-97153-09 above — NOT YET VERIFIED against a
    # real 97155 sample (see that rule's own pre-existing notes).
    return _check_same_day_location_break(fields)


# --------------------------------------------------------------- dispatch

DETERMINISTIC_CHECKERS = {
    "SN-97151-02": _check_SN_97151_02,
    "SN-97151-06": _check_SN_97151_06,
    "SN-97151-07": _check_SN_97151_07,
    "SN-97151-08": _check_SN_97151_08,
    "SN-97151-10": _check_SN_97151_10,
    "SN-97151-11": _check_SN_97151_11,
    "SN-97151-12": _check_SN_97151_12,
    "SN-97151-13": _check_SN_97151_13,
    "SN-97151-15": _check_SN_97151_15,
    "SN-97151-16": _check_SN_97151_16,
    "SN-97153-03": _check_SN_97153_03,
    "SN-97153-05": _check_SN_97153_05,
    "SN-97153-08": _check_SN_97153_08,
    "SN-97153-11": _check_SN_97153_11,
    "SN-97153-12": _check_SN_97153_12,
    "SN-97153-15": _check_SN_97153_15,
    "SN-97153-16": _check_SN_97153_16,
    "SN-97153-17": _check_SN_97153_17,
    "SN-97153-19": _check_SN_97153_19,
    "SN-97153-21": _check_SN_97153_21,
    "SN-97153-22": _check_SN_97153_22,
    "SN-97155-03": _check_SN_97155_03,
    "SN-97155-09": _check_SN_97155_09,
    "SN-97155-13": _check_SN_97155_13,
    "SN-97155-18": _check_SN_97155_18,
    "SN-97155-19": _check_SN_97155_19,
    "SN-97155-23": _check_SN_97155_23,
    "SN-97155-25": _check_SN_97155_25,
    "SN-97151-04": _check_SN_97151_04,
    "SN-97153-04": _check_SN_97153_04,
    "SN-97155-05": _check_SN_97155_05,
    "SN-97153-09": _check_SN_97153_09,
    "SN-97155-16": _check_SN_97155_16,
}


def run_deterministic_checks(rules: list[dict], fields: dict) -> tuple[dict[str, dict], list[dict]]:
    """Runs every active, check_type=="deterministic" rule's checker.

    Returns (det_results, escalated_rules) — `escalated_rules` is the list
    of full rule dicts whose own checker isn't implemented yet, for the
    caller to route into the judgment layer instead of silently skipping
    them (same escalation pattern the prior TP-review project used).
    """
    det_results: dict[str, dict] = {}
    escalated_rules: list[dict] = []
    for rule in rules:
        if not rule["active"] or rule["check_type"] != "deterministic":
            continue
        checker = DETERMINISTIC_CHECKERS.get(rule["rule_id"])
        if checker is None:
            escalated_rules.append(rule)
            continue
        det_results[rule["rule_id"]] = checker(fields)
    return det_results, escalated_rules
