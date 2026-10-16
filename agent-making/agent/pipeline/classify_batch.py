"""Cover-page boundary detection: turns one batch PDF's page list into
"here's who's in it, and which pages belong to each of their documents".

No model calls anywhere in this module — every field is extracted with
plain text parsing against patterns confirmed directly from real sample
Activity Statement PDFs. A page range that doesn't clearly resolve is
flagged in `unresolved`, never silently guessed at.

REAL BUG FOUND AND FIXED (2026-10-15): a real client's own 961-page export
("500_Session_Notes_Sample.pdf", 184 real session-note documents across
50 real patients, confirmed directly against the file) uses "Invoice -
<name>" as its own per-patient cover/boundary page, never "Activity
Statement - <name>" at all. _COVER_RE only ever recognized the latter, so
this entire real file matched ZERO cover pages and fell into one giant
`unresolved` blob (pages 1-961) -- not a spend-cap problem (that was never
even reached: zero documents were ever confidently classified, so zero
real calls were ever made), a classification-format gap. Confirmed via
the real file: exactly 50 "Invoice - <name>" pages, 50 distinct real
patient names, and the SAME real appendix-footer/Patient-DOB structure on
every one of the 911 real content pages that follow — this is genuinely
the same per-patient-boundary ROLE as an Activity Statement cover page,
just a different label this format uses. Fixed by recognizing BOTH labels
as valid boundary markers; everything downstream (classify_person_docs.py,
fields.py) is unchanged, since the real per-document structure after the
cover page is identical either way.

One real, known, honest gap this does NOT fix: an "Invoice -" cover page
carries billing rows (date/service/units/$ amount), never a real session
START/END TIME range the way an "Activity Statement -" cover page does
(see activity_statement.py's own _TIME_ANCHOR_RE) -- so timesheet-
alignment rules (SN-*-04/SN-*-09 etc.) correctly resolve `not_checkable`
for a batch classified via this cover format, the same honest fallback
already used whenever no real timesheet data exists, not a wrong guess.

HAIKU 5.5 FALLBACK (2026-10-15): a LAST-RESORT, real, billed fallback for a
page range every regex check above has already failed to resolve. Never
runs on a section the regex path resolves on its own -- it only fires at
the exact points this module was already about to give up and write an
`unresolved` entry. Deliberately not a redesign: the regex path stays
primary and free for every section it can actually read; this just gives a
genuinely unreadable/novel-format section one more real attempt before
flagging it, instead of guaranteed-unresolved. A fallback result that
doesn't pass real sanity checks (parseable DOB, parseable service codes,
in-bounds page ranges) is discarded -- never trusted blindly just because
a model produced it; the section falls back to the same honest
`unresolved` entry the regex path would have written anyway.
"""
import re

from .classify_person_docs import ClassificationAmbiguous, classify_person_docs
from .model_provider import CLASSIFICATION_FALLBACK_MODEL, CallTracker, call_tool_json
from .person_identity import global_key, normalize_dob

_COVER_RE = re.compile(r"(?:Activity Statement|Invoice) - (.+)")
_APPENDIX_NOTE_REF_RE = re.compile(r"Notes:\s*see Appendix\s+[IVXLC]+", re.IGNORECASE)
_APPENDIX_FOOTER_RE = re.compile(r"[IVXLC]+\s*-\s*Page\s+\d+\s+of\s+\d+")
_DOB_RE = re.compile(r"Patient DOB:\s*(\d{2}/\d{2}/\d{4})")
_SERVICE_CODE_SHAPE_RE = re.compile(r"^\d{5}$")

# A hint only, never the sole signal — the real determinant is structural
# (no billable service line, no attached note pages). A differently-named
# admin/no-show cover page must be caught the same way.
_ADMIN_NAME_HINTS = {"admin client"}

_FALLBACK_TOOL_NAME = "record_unresolved_section_classification"
_FALLBACK_TOOL_DESCRIPTION = (
    "Record whether this page range from a batch session-note PDF clearly belongs to ONE identifiable "
    "patient with at least one real, attached session-note document -- and if so, that patient's name, "
    "DOB, and each attached document's own service code / date of service / page range. Set "
    "resolvable=false (and leave every other field null/empty) if the range is genuinely ambiguous, "
    "unreadable, mixes more than one patient, or isn't a real session-note section at all -- never guess."
)
_FALLBACK_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "resolvable": {
            "type": "boolean",
            "description": "True only if this entire page range confidently belongs to one patient.",
        },
        "full_name": {"type": ["string", "null"], "description": "The patient's full name as printed, or null."},
        "dob": {"type": ["string", "null"], "description": "The patient's DOB as printed, MM/DD/YYYY, or null."},
        "documents": {
            "type": "array",
            "description": "One entry per real session-note document found in this page range.",
            "items": {
                "type": "object",
                "properties": {
                    "service_code": {"type": "string", "description": "The 5-digit service code, e.g. '97153'."},
                    "date_of_service": {"type": ["string", "null"], "description": "MM/DD/YYYY as printed, or null."},
                    "page_start": {"type": "integer", "description": "First real page number of this document."},
                    "page_end": {"type": "integer", "description": "Last real page number of this document."},
                },
                "required": ["service_code", "date_of_service", "page_start", "page_end"],
            },
        },
    },
    "required": ["resolvable", "full_name", "dob", "documents"],
}


def _iso_date(mmddyyyy: str) -> str:
    return normalize_dob(mmddyyyy).isoformat()


def _fallback_cost_usd(tracker: CallTracker) -> float:
    """CallTracker.estimated_cost_usd now prices per real MODEL (fixed
    2026-10-15 — see model_provider.py's own CallTracker docstring), so a
    claude-haiku-5-5 call made via model_override="anthropic:..." is
    already priced correctly at its own real rate here, not Sonnet's."""
    return tracker.estimated_cost_usd


def _sanity_check_fallback_result(result: dict, page_start: int, page_end: int) -> dict | None:
    """Never trust a model's structured output just because it parsed --
    confirm it's internally coherent against the real page range before
    treating it as a resolved person. Returns a people[]-shaped entry on
    success, or None if anything doesn't check out (caller keeps the
    original `unresolved` entry in that case).
    """
    if not result.get("resolvable"):
        return None

    full_name = (result.get("full_name") or "").strip()
    dob = (result.get("dob") or "").strip()
    documents_raw = result.get("documents") or []
    if not full_name or not dob or not documents_raw:
        return None

    try:
        key = global_key(full_name, dob)
        iso_dob = _iso_date(dob)
    except ValueError:
        return None

    documents = []
    for doc in documents_raw:
        service_code = str(doc.get("service_code") or "")
        doc_start = doc.get("page_start")
        doc_end = doc.get("page_end")
        date_of_service = doc.get("date_of_service")
        if not _SERVICE_CODE_SHAPE_RE.match(service_code):
            return None
        if not isinstance(doc_start, int) or not isinstance(doc_end, int) or doc_start > doc_end:
            return None
        if doc_start < page_start or doc_end > page_end:
            return None
        try:
            iso_date_of_service = _iso_date(date_of_service) if date_of_service else None
        except ValueError:
            return None
        documents.append({
            "appendix": None,
            "service_code": service_code,
            "date_of_service": iso_date_of_service,
            "page_start": doc_start,
            "page_end": doc_end,
        })

    return {
        "global_key": key,
        "full_name": full_name,
        "dob": iso_dob,
        "cover_page": page_start,
        "documents": documents,
        "resolved_via": "haiku_5_5_fallback",
    }


def _attempt_haiku_fallback(pages: list[dict], page_start: int, page_end: int, *, batch_label: str) -> dict | None:
    """Last-resort real call for a page range the regex path could not
    resolve. Returns a people[]-shaped entry on a confident, sanity-checked
    success, or None (caller keeps its own original `unresolved` entry) on
    any failure, low confidence, or malformed result -- deliberately broad
    exception handling, since a failure here (a disallowed real call, a
    network error, a malformed response) must degrade to the existing
    honest `unresolved` behavior, never crash the whole batch's
    classification.
    """
    section_text = "\n\n".join(f"--- Page {p['page_number']} ---\n{p['text']}" for p in pages)
    prompt = (
        "The following is a real page range from an ABA session-note batch PDF. A deterministic, "
        "regex-based classifier could not resolve which patient this range belongs to or how it splits "
        "into documents. Read the text and decide for real -- do not guess if it's genuinely unclear.\n\n"
        f"{section_text}"
    )
    try:
        tracker = CallTracker()
        result = call_tool_json(
            prompt_text=prompt,
            tool_name=_FALLBACK_TOOL_NAME,
            tool_description=_FALLBACK_TOOL_DESCRIPTION,
            input_schema=_FALLBACK_INPUT_SCHEMA,
            tracker=tracker,
            model_override=f"anthropic:{CLASSIFICATION_FALLBACK_MODEL}",
            call_reason="classification_fallback",
            enable_anthropic_fallback=False,
        )
    except Exception as exc:
        print(
            f"[classify_batch] Haiku fallback FAILED for pages {page_start}-{page_end} "
            f"({batch_label}): {exc}"
        )
        return None

    cost = _fallback_cost_usd(tracker)
    person = _sanity_check_fallback_result(result, page_start, page_end)
    print(
        f"[classify_batch] Haiku fallback FIRED for pages {page_start}-{page_end} ({batch_label}): "
        f"model_said_resolvable={result.get('resolvable')}, accepted={person is not None}, cost=${cost:.5f}"
    )
    return person


def _resolve_or_fallback(
    unresolved: list[dict], fallback_pages: list[dict], page_start: int, page_end: int, note: str, batch_label: str,
) -> dict | None:
    """Shared tail end of every unresolved branch below: try the Haiku
    fallback on exactly the pages this range covers, and only write the
    original honest `unresolved` entry if the fallback also didn't produce
    a sanity-checked result (or wasn't attempted because it failed/errored).
    """
    person = _attempt_haiku_fallback(fallback_pages, page_start, page_end, batch_label=batch_label)
    if person is not None:
        return person
    unresolved.append({"page_start": page_start, "page_end": page_end, "note": note})
    return None


def classify_batch(pages: list[dict], batch_label: str = "") -> dict:
    """Returns {"people": [...], "admin_noise_pages": [...], "unresolved": [...]}
    — see agent.pipeline.api.classify_batch_pdf for the full shape.

    `batch_label` is a free-text identifier (e.g. the source filename) used
    only for the Haiku fallback's own log lines below, so real-world
    fallback frequency is traceable back to a real upload, not just a page
    range in the abstract.
    """
    cover_indices = [i for i, p in enumerate(pages) if _COVER_RE.search(p["text"])]

    if not cover_indices:
        if not pages:
            return {"people": [], "admin_noise_pages": [], "unresolved": []}
        unresolved: list[dict] = []
        person = _resolve_or_fallback(
            unresolved, pages, pages[0]["page_number"], pages[-1]["page_number"],
            "no 'Activity Statement - <name>' cover page found anywhere in this document", batch_label,
        )
        return {
            "people": [person] if person is not None else [],
            "admin_noise_pages": [],
            "unresolved": unresolved,
        }

    people = []
    admin_noise_pages = []
    unresolved = []

    for position, cover_idx in enumerate(cover_indices):
        cover_page = pages[cover_idx]
        name = _COVER_RE.search(cover_page["text"]).group(1).strip()

        range_end_idx = (
            cover_indices[position + 1] - 1 if position + 1 < len(cover_indices) else len(pages) - 1
        )
        content_pages = pages[cover_idx + 1: range_end_idx + 1]

        has_appendix_ref = bool(_APPENDIX_NOTE_REF_RE.search(cover_page["text"]))
        has_appendix_content = any(_APPENDIX_FOOTER_RE.search(p["text"]) for p in content_pages)

        if not has_appendix_ref and not has_appendix_content:
            reason = "cover page has no billable service line referencing an appendix and no attached note pages"
            if name.strip().lower() in _ADMIN_NAME_HINTS:
                reason += " (name matches the known admin-noise pattern)"
            admin_noise_pages.append({
                "page_start": cover_page["page_number"],
                "page_end": content_pages[-1]["page_number"] if content_pages else cover_page["page_number"],
                "reason": reason,
            })
            continue

        if not content_pages:
            person = _resolve_or_fallback(
                unresolved, [cover_page], cover_page["page_number"], cover_page["page_number"],
                f"cover page for '{name}' references an appendix but no content pages follow it before the next cover page",
                batch_label,
            )
            if person is not None:
                people.append(person)
            continue

        section_pages = [cover_page] + content_pages
        section_start, section_end = cover_page["page_number"], content_pages[-1]["page_number"]

        low_text_page = next((p for p in content_pages if p["low_text"]), None)
        if low_text_page is not None:
            person = _resolve_or_fallback(
                unresolved, section_pages, section_start, section_end,
                f"page {low_text_page['page_number']} in '{name}'s attached pages has almost no extractable text (likely a scanned image with no text layer)",
                batch_label,
            )
            if person is not None:
                people.append(person)
            continue

        dob_match = next((_DOB_RE.search(p["text"]) for p in content_pages if _DOB_RE.search(p["text"])), None)
        if dob_match is None:
            person = _resolve_or_fallback(
                unresolved, section_pages, section_start, section_end,
                f"could not find a 'Patient DOB:' field anywhere in '{name}'s attached pages", batch_label,
            )
            if person is not None:
                people.append(person)
            continue

        try:
            key = global_key(name, dob_match.group(1))
        except ValueError as exc:
            person = _resolve_or_fallback(
                unresolved, section_pages, section_start, section_end,
                f"could not derive a global_key for '{name}': {exc}", batch_label,
            )
            if person is not None:
                people.append(person)
            continue

        try:
            documents = classify_person_docs(content_pages)
        except ClassificationAmbiguous as exc:
            person = _resolve_or_fallback(
                unresolved, section_pages, section_start, section_end,
                f"'{name}'s attached pages did not cleanly split into documents: {exc}", batch_label,
            )
            if person is not None:
                people.append(person)
            continue

        people.append({
            "global_key": key,
            "full_name": name,
            "dob": _iso_date(dob_match.group(1)),
            "cover_page": cover_page["page_number"],
            "documents": documents,
        })

    return {"people": people, "admin_noise_pages": admin_noise_pages, "unresolved": unresolved}
