"""Within one person's page range: split by Appendix marker into the
individual service-code documents bundled under one cover page, and confirm
each document's real page boundaries from its own printed footer — never
assume a fixed page count for a given service code (a 97153 note can be 3,
4, or 5 pages depending on content, confirmed directly from real sample PDFs).
"""
import re

from .person_identity import normalize_dob

_FOOTER_RE = re.compile(r"([IVXLC]+)\s*-\s*Page\s+(\d+)\s+of\s+(\d+)")
_SERVICE_CODE_RE = re.compile(r"Service Code:\s*(\d{5})")
_SESSION_DATE_RE = re.compile(r"Session Date:\s*(\d{2}/\d{2}/\d{4})")


class ClassificationAmbiguous(Exception):
    """Raised when a person's content pages don't cleanly resolve into
    documents — the caller must route this into the unresolved bucket
    rather than silently returning a possibly-wrong split. A genuine
    "couldn't determine" must never look like a confident answer.
    """


def _iso_date(mmddyyyy: str) -> str:
    return normalize_dob(mmddyyyy).isoformat()


def _flush(group: list[dict], appendix: str | None) -> dict:
    first_page = group[0]
    footer_matches = [_FOOTER_RE.search(p["text"]) for p in group]

    if appendix is not None:
        if any(m is None for m in footer_matches):
            raise ClassificationAmbiguous(
                f"appendix {appendix} document (pages {first_page['page_number']}-"
                f"{group[-1]['page_number']}) has a page without a parseable "
                "'<roman> - Page N of M' footer"
            )
        expected_total = int(footer_matches[0].group(3))
        if expected_total != len(group):
            raise ClassificationAmbiguous(
                f"appendix {appendix} document's own footer claims {expected_total} "
                f"page(s) but {len(group)} page(s) were actually found in this range "
                f"(pages {first_page['page_number']}-{group[-1]['page_number']})"
            )
        for offset, match in enumerate(footer_matches, start=1):
            if int(match.group(2)) != offset:
                raise ClassificationAmbiguous(
                    f"appendix {appendix} document's pages are out of order or "
                    f"missing: expected local page {offset}, footer says "
                    f"{match.group(2)} (pages {first_page['page_number']}-"
                    f"{group[-1]['page_number']})"
                )

    service_match = _SERVICE_CODE_RE.search(first_page["text"])
    date_match = _SESSION_DATE_RE.search(first_page["text"])
    if service_match is None:
        raise ClassificationAmbiguous(
            f"document at pages {first_page['page_number']}-{group[-1]['page_number']} "
            "has no parseable 'Service Code:' field on its own first page"
        )
    if date_match is None:
        raise ClassificationAmbiguous(
            f"document at pages {first_page['page_number']}-{group[-1]['page_number']} "
            "has no parseable 'Session Date:' field on its own first page"
        )

    return {
        "appendix": appendix,
        "service_code": service_match.group(1),
        "date_of_service": _iso_date(date_match.group(1)),
        "page_start": first_page["page_number"],
        "page_end": group[-1]["page_number"],
    }


def classify_person_docs(content_pages: list[dict]) -> list[dict]:
    """Returns documents: [{appendix, service_code, date_of_service,
    page_start, page_end}] for one person's page range. Raises
    ClassificationAmbiguous if any resulting group's own footer doesn't
    confirm the split — never returns a guessed boundary.
    """
    if not content_pages:
        return []

    documents = []
    current_group: list[dict] = []
    current_appendix: str | None = None

    for page in content_pages:
        footer_match = _FOOTER_RE.search(page["text"])
        # A page whose own footer doesn't parse (missing/corrupted, not
        # simply untagged) carries forward the group it's already inside,
        # rather than being treated as its own boundary — that would
        # otherwise silently truncate the group before ever reaching the
        # "page without a parseable footer" check in _flush below. Only a
        # genuinely DIFFERENT, well-formed roman-numeral tag starts a new
        # group.
        appendix = footer_match.group(1) if footer_match else current_appendix
        if current_group and appendix != current_appendix and appendix is not None:
            documents.append(_flush(current_group, current_appendix))
            current_group = []
        current_appendix = appendix
        current_group.append(page)

    if current_group:
        documents.append(_flush(current_group, current_appendix))

    return documents
