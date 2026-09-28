"""Cover-page boundary detection: turns one batch PDF's page list into
"here's who's in it, and which pages belong to each of their documents".

No model calls anywhere in this module — every field is extracted with
plain text parsing against patterns confirmed directly from real sample
Activity Statement PDFs. A page range that doesn't clearly resolve is
flagged in `unresolved`, never silently guessed at.
"""
import re

from .classify_person_docs import ClassificationAmbiguous, classify_person_docs
from .person_identity import global_key, normalize_dob

_COVER_RE = re.compile(r"Activity Statement - (.+)")
_APPENDIX_NOTE_REF_RE = re.compile(r"Notes:\s*see Appendix\s+[IVXLC]+", re.IGNORECASE)
_APPENDIX_FOOTER_RE = re.compile(r"[IVXLC]+\s*-\s*Page\s+\d+\s+of\s+\d+")
_DOB_RE = re.compile(r"Patient DOB:\s*(\d{2}/\d{2}/\d{4})")

# A hint only, never the sole signal — the real determinant is structural
# (no billable service line, no attached note pages). A differently-named
# admin/no-show cover page must be caught the same way.
_ADMIN_NAME_HINTS = {"admin client"}


def _iso_date(mmddyyyy: str) -> str:
    return normalize_dob(mmddyyyy).isoformat()


def classify_batch(pages: list[dict]) -> dict:
    """Returns {"people": [...], "admin_noise_pages": [...], "unresolved": [...]}
    — see agent.pipeline.api.classify_batch_pdf for the full shape.
    """
    cover_indices = [i for i, p in enumerate(pages) if _COVER_RE.search(p["text"])]

    if not cover_indices:
        if not pages:
            return {"people": [], "admin_noise_pages": [], "unresolved": []}
        return {
            "people": [],
            "admin_noise_pages": [],
            "unresolved": [{
                "page_start": pages[0]["page_number"],
                "page_end": pages[-1]["page_number"],
                "note": "no 'Activity Statement - <name>' cover page found anywhere in this document",
            }],
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
            unresolved.append({
                "page_start": cover_page["page_number"],
                "page_end": cover_page["page_number"],
                "note": f"cover page for '{name}' references an appendix but no content pages follow it before the next cover page",
            })
            continue

        low_text_page = next((p for p in content_pages if p["low_text"]), None)
        if low_text_page is not None:
            unresolved.append({
                "page_start": cover_page["page_number"],
                "page_end": content_pages[-1]["page_number"],
                "note": f"page {low_text_page['page_number']} in '{name}'s attached pages has almost no extractable text (likely a scanned image with no text layer)",
            })
            continue

        dob_match = next((_DOB_RE.search(p["text"]) for p in content_pages if _DOB_RE.search(p["text"])), None)
        if dob_match is None:
            unresolved.append({
                "page_start": cover_page["page_number"],
                "page_end": content_pages[-1]["page_number"],
                "note": f"could not find a 'Patient DOB:' field anywhere in '{name}'s attached pages",
            })
            continue

        try:
            key = global_key(name, dob_match.group(1))
        except ValueError as exc:
            unresolved.append({
                "page_start": cover_page["page_number"],
                "page_end": content_pages[-1]["page_number"],
                "note": f"could not derive a global_key for '{name}': {exc}",
            })
            continue

        try:
            documents = classify_person_docs(content_pages)
        except ClassificationAmbiguous as exc:
            unresolved.append({
                "page_start": cover_page["page_number"],
                "page_end": content_pages[-1]["page_number"],
                "note": f"'{name}'s attached pages did not cleanly split into documents: {exc}",
            })
            continue

        people.append({
            "global_key": key,
            "full_name": name,
            "dob": _iso_date(dob_match.group(1)),
            "cover_page": cover_page["page_number"],
            "documents": documents,
        })

    return {"people": people, "admin_noise_pages": admin_noise_pages, "unresolved": unresolved}
