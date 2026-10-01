"""Parses the real, structured per-session rows off an Activity Statement
cover page (the SAME "Activity Statement - <name>" page
classify_batch.py's own `_COVER_RE` already uses as a page-boundary
marker — see that module's own docstring) into plain data: date, start/
end time, which appendix (if any) it's billing for, and location.

This is a REAL second data source that was sitting unused: classify_batch.py
already finds and records each person's `cover_page` number, but nothing
downstream ever read the table on that page — every "does the note align
with the timesheet?" rule was unconditionally stubbed `not_checkable`
with "needs a timesheet — not available to this pipeline", even though
the timesheet was right there in the same uploaded PDF the whole time.
See rules.json's own corrected notes on SN-97151-04/SN-97153-.../
SN-97155-05/SN-97155-21 for where this is actually used.

Plain regex parsing, no model call — the table format is fixed/tabular,
confirmed directly against real sample Activity Statement pages (see
agent-making/agent/tests/fixtures/), not drafted from a guess at the
format.
"""
from __future__ import annotations

import re

_TIME_ANCHOR_RE = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s*to\s*(\d{1,2}:\d{2}\s*[AP]M)",
    re.DOTALL,
)
_SERVICE_CODE_RE = re.compile(r"(?<!\d)(\d{5}):")
_LOCATION_RE = re.compile(r"(?<!\d)(\d{1,2}):\s*(.+)", re.DOTALL)
_APPENDIX_RE = re.compile(r"Notes:\s*see Appendix\s+([IVXLC]+)", re.IGNORECASE)
_END_BOUNDARY_RE = re.compile(r"Notes:|Amount Paid:")


def parse_activity_statement_rows(text: str) -> list[dict]:
    """Every row on one Activity Statement page — one dict per row:
    {date (MM/DD/YYYY str), start_time, end_time (both "H:MM AM/PM" str,
    whitespace-normalized), service_code (5-digit str, or None for a
    non-billable row like "NB: Prep Time"/"NB: Lunch Break"), appendix
    (roman numeral str this row's "Notes: see Appendix <X>" line
    references, or None for a non-billable row with no appendix note),
    location (str, or None if genuinely not parseable)}.

    Each billable row anchors to the SAME PersonDocument.appendix value
    classify_batch.py already resolves independently (from the exact same
    "Notes: see Appendix <X>" text, on the cover page there instead of the
    session note's own pages) — this is the reliable join key between a
    timesheet row and a specific reviewed document, not date/service_code
    guessing.
    """
    anchors = list(_TIME_ANCHOR_RE.finditer(text))
    rows = []
    for i, m in enumerate(anchors):
        chunk_end = anchors[i + 1].start() if i + 1 < len(anchors) else len(text)
        chunk = text[m.end():chunk_end]

        svc_match = _SERVICE_CODE_RE.search(chunk)
        appendix_match = _APPENDIX_RE.search(chunk)
        boundary_match = _END_BOUNDARY_RE.search(chunk)
        before_boundary = chunk[:boundary_match.start()] if boundary_match else chunk
        loc_matches = list(_LOCATION_RE.finditer(before_boundary))
        location = re.sub(r"\s+", " ", loc_matches[-1].group(2)).strip() if loc_matches else None

        rows.append({
            "date": m.group(1),
            "start_time": re.sub(r"\s+", " ", m.group(2)).strip(),
            "end_time": re.sub(r"\s+", " ", m.group(3)).strip(),
            "service_code": svc_match.group(1) if svc_match else None,
            "appendix": appendix_match.group(1) if appendix_match else None,
            "location": location,
        })
    return rows


def row_for_appendix(rows: list[dict], appendix: str | None) -> dict | None:
    """The one row billing for a specific PersonDocument's own appendix —
    None if this document has no appendix (nothing to match against) or
    no row on the timesheet references it."""
    if not appendix:
        return None
    return next((r for r in rows if r["appendix"] == appendix), None)
