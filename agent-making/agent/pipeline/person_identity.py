"""Turns a printed name + DOB into a stable, derived person key.
`Person.global_key` in the backend data model is this value — never
hand-typed, always computed the same way from the same two inputs (see
CLAUDE.md's invariant on this).
"""
import re
from datetime import date, datetime

_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_DOB_FORMATS = ("%m/%d/%Y", "%Y-%m-%d")


def normalize_name(full_name: str) -> tuple[str, str]:
    """Returns (last_name, first_name), both lowercased and stripped of
    punctuation. The last whitespace-separated token is taken as the last
    name; everything before it is joined (no separator) as the first name —
    this only needs to be internally consistent, not a real name parser.
    """
    cleaned = _NON_ALNUM_RE.sub("", full_name.lower()).strip()
    tokens = cleaned.split()
    if not tokens:
        raise ValueError(f"cannot normalize empty or unparseable name: {full_name!r}")
    last_name = tokens[-1]
    first_name = "".join(tokens[:-1]) or last_name
    return last_name, first_name


def normalize_dob(dob: str) -> date:
    """Accepts either MM/DD/YYYY (as printed on these documents) or
    YYYY-MM-DD (already-ISO input, e.g. from a re-classification call)."""
    cleaned = dob.strip()
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"cannot parse DOB: {dob!r}")


def global_key(full_name: str, dob: str) -> str:
    """lastname_firstname_YYYYMMDD, fully normalized and derived — the join
    key every batch upload resolves a split-out person against."""
    last_name, first_name = normalize_name(full_name)
    dob_date = normalize_dob(dob)
    return f"{last_name}_{first_name}_{dob_date:%Y%m%d}"
