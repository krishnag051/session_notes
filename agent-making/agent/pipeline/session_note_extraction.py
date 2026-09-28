"""Round 59, Step 1: extracts a structured JSON from a single uploaded
session-note file -- session date, session location, the clinician's
telehealth location (when applicable), the patient's telehealth location
(when applicable), and which assessment-activity checkbox is marked
(Review records / Interview / Direct observation / Treatment plan
development / a named tool like VB-MAPP, ABLLS, AFLS, Vineland, Functional
Analysis).

Same honest-confidence philosophy as supporting_doc_extraction.py: a field
that isn't actually in the document comes back confidence="none",
value=None -- never a guess, never silently blank without saying so.

Model call goes through pipeline/model_provider.py's call_tool_json --
defaults to the free OpenRouter model for building/testing (see that
module's own docstring for exactly why), never Anthropic unless a human
explicitly passes model_override="anthropic:..." with separate per-
instance approval, same standing rule as every other real Anthropic call
site in this pipeline.

Local caching only (Round 59) -- NOT a database table. Keyed by a SHA-256
content hash of the file's own bytes, so re-processing the identical file
(e.g. a retried pipeline run, or re-running this round's own tests) never
repeats the model call. Lives under agent-making/agent/.cache/
session_note_extractions/ -- explicitly NOT a durable, backend-queryable
record; a real permanent version of this (so the backend/frontend can show
and reuse cached results across restarts, independent of whichever
machine ran the extraction) is real backend/DB work, deliberately deferred
to a future round. Losing this cache (e.g. wiping the directory) only
means the NEXT read re-runs the extraction call -- it is not the source of
truth for anything.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .extract import extract_pdf_text
from .model_provider import CallTracker, ModelCallError, call_tool_json

# Live incident fix (2026-08): a session-note file whose extraction hit a
# CONFIRMED-TRANSIENT upstream failure (model_provider.py's own retry loop
# already exhausted its attempts) must not raise past this point -- one
# file's temporary bad luck previously killed the ENTIRE upload's real
# review (every other rule's already-computed finding discarded along
# with it), the exact same class of bug Round 78's integrity.py fix
# addressed for a persistently-missing judgment rule_id. This sentinel key
# is how session_note_comparison.py (the only real consumer of this
# module's output) tells "this file's extraction genuinely failed due to
# an upstream problem" apart from "this file was extracted fine and
# simply doesn't mention this field" -- the two must never look the same,
# or a real infra failure would silently read as "the note states
# nothing," which is a different, misleading claim.
EXTRACTION_ERROR_KEY = "_extraction_error"

CONFIDENCE_LEVELS = ("high", "medium", "low", "none")

SESSION_NOTE_FIELDS = (
    "session_date",
    "session_location",
    "clinician_telehealth_location",
    "patient_telehealth_location",
    "assessment_activity",
    # Fix Round (2026-08-27): backs QA-COC-01's "session note detailed"
    # half -- previously never extracted at all, so that rule's judgment
    # call had zero real session-note content to read (confirmed dead-
    # description last round). Same {value, confidence, source_quote}
    # shape as every other field here -- no schema restructuring needed.
    # Zero new spend: this rides the SAME already-existing, already-free
    # (OpenRouter) per-note extraction call every upload with session
    # notes already makes -- not a new call.
    "note_detail_level",
)

_FIELD_DESCRIPTIONS = {
    "session_date": "The date this session/note itself took place.",
    "session_location": (
        "The place of service (POS) for this session -- e.g. Home, Office, Community, Telehealth, School."
    ),
    "clinician_telehealth_location": (
        "If this session was conducted (even partly) via telehealth, where the CLINICIAN was physically "
        "located during the session. Null/none if the session wasn't telehealth at all, or the note doesn't say."
    ),
    "patient_telehealth_location": (
        "If this session was conducted (even partly) via telehealth, where the PATIENT was physically "
        "located during the session. Null/none if the session wasn't telehealth at all, or the note doesn't say."
    ),
    "assessment_activity": (
        "Which assessment-activity checkbox is marked in this note -- e.g. 'Review records', 'Interview', "
        "'Direct observation', 'Treatment plan development', or a named assessment tool "
        "(VB-MAPP, ABLLS, AFLS, Vineland, Functional Analysis, etc.). Report exactly what's marked/named, "
        "not your own inference of what activity probably happened."
    ),
    "note_detail_level": (
        "Whether this note's own narrative describes actual session CONTENT (the specific topic discussed "
        "and its relevance to treatment), or only confirms that contact happened with no real content named. "
        "Set value to exactly \"detailed\" or \"minimal\" (never anything else). "
        "\"detailed\" example: 'Discussed clients social goals as well as maladaptive behaviors and how to "
        "implement the replacement behaviors.' -- names the actual topic and its treatment relevance. "
        "\"minimal\" example: 'Discussed patient.' or 'Spoke with provider regarding treatment.' -- confirms "
        "contact occurred but names no actual content. Base this only on the note's own narrative text, not "
        "on the structured fields above."
    ),
}

EXTRACTION_TOOL_NAME = "record_session_note_extraction"
EXTRACTION_TOOL_DESCRIPTION = (
    "Record the extracted value for each required field from this session note. Every field is required in "
    "the response, even when the document doesn't contain it -- in that case, set value to null and "
    "confidence to \"none\"."
)

EXTRACTION_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        field: {
            "type": "object",
            "properties": {
                "value": {
                    "type": ["string", "null"],
                    "description": "The extracted value as found in the document, or null if not present.",
                },
                "confidence": {
                    "type": "string",
                    "enum": list(CONFIDENCE_LEVELS),
                    "description": (
                        "\"none\" means this document does not contain this information -- an expected, "
                        "honest outcome, not a failure. \"low\"/\"medium\"/\"high\" reflect how directly and "
                        "unambiguously the document states the value."
                    ),
                },
                "source_quote": {
                    "type": ["string", "null"],
                    "description": "A short verbatim quote from the document supporting this value, or null.",
                },
            },
            "required": ["value", "confidence", "source_quote"],
        }
        for field in SESSION_NOTE_FIELDS
    },
    "required": list(SESSION_NOTE_FIELDS),
}


def _empty_field_result() -> dict[str, Any]:
    return {"value": None, "confidence": "none", "source_quote": None}


def _extraction_failed_result(error_message: str) -> dict[str, Any]:
    """Same shape every caller already expects (all 5 SESSION_NOTE_FIELDS
    present, each confidence="none") PLUS the EXTRACTION_ERROR_KEY sentinel
    -- so a caller that doesn't know about this sentinel (e.g. the
    backend's own Pydantic model, which only knows the 5 named fields)
    still gets something well-formed and harmless, while
    session_note_comparison.py (which DOES know about it) can tell this
    apart from a genuine "the note doesn't mention this" result and
    produce a real, distinctive not_checkable finding instead of a
    misleading "field absent" one.
    """
    result = {field: _empty_field_result() for field in SESSION_NOTE_FIELDS}
    result[EXTRACTION_ERROR_KEY] = error_message
    return result


def _build_prompt(full_text: str) -> str:
    field_list = "\n".join(f"- {field}: {_FIELD_DESCRIPTIONS[field]}" for field in SESSION_NOTE_FIELDS)
    return (
        "You are extracting specific factual fields from an ABA session note. Extract each of the following "
        f"fields if present:\n\n{field_list}\n\n"
        "For each field: if the document clearly states it, extract the value and a short supporting quote, "
        "with a confidence level reflecting how directly it's stated. If the document does NOT contain this "
        "information, you MUST still report the field with value=null and confidence=\"none\" -- never guess, "
        "never leave a field out, and never report a value that isn't actually in the document.\n\n"
        "Session note text follows:\n\n" + full_text
    )


def _normalize(extracted: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Defensive normalization, not trust-by-default: guarantee every field
    is present with a valid confidence level even if the model's tool call
    somehow omits one -- same discipline as supporting_doc_extraction.py's
    own normalization step.
    """
    result: dict[str, dict[str, Any]] = {}
    for field in SESSION_NOTE_FIELDS:
        entry = extracted.get(field)
        if not isinstance(entry, dict) or entry.get("confidence") not in CONFIDENCE_LEVELS:
            result[field] = _empty_field_result()
        else:
            result[field] = {
                "value": entry.get("value"),
                "confidence": entry["confidence"],
                "source_quote": entry.get("source_quote"),
            }
    return result


def extract_session_note_text(
    full_text: str,
    *,
    tracker: CallTracker,
    model_override: str | None = None,
) -> dict[str, dict[str, Any]]:
    """The core extraction call, over already-extracted text (no file I/O,
    no caching) -- kept separate from extract_session_note_file below so
    tests can exercise the model-call mechanics without needing a real
    file on disk, and so a future caller that already has text some other
    way (e.g. a pasted-in note) doesn't need a fake file either.

    Live incident fix (2026-08): catches a TransientModelCallError that
    survived model_provider.py's own retry-with-backoff loop (i.e.
    genuinely exhausted, not a first-attempt failure) and returns an
    honest failure marker instead of raising -- see
    _extraction_failed_result's own docstring for why this must be
    visibly distinct from "the note doesn't mention this field."

    2026-08-13: broadened to catch the base ModelCallError, not just
    TransientModelCallError -- call_tool_json's OpenRouter path now falls
    back to a real Anthropic call before ever raising (see
    model_provider.py::call_openrouter_with_fallback), so anything that
    still reaches this except clause means BOTH OpenRouter (with retries)
    AND the Anthropic fallback were exhausted -- a stronger, not weaker,
    failure signal than before this fix, and still exactly the case this
    honest-failure-marker behavior exists for.
    """
    prompt = _build_prompt(full_text)
    try:
        raw = call_tool_json(
            prompt_text=prompt,
            tool_name=EXTRACTION_TOOL_NAME,
            tool_description=EXTRACTION_TOOL_DESCRIPTION,
            input_schema=EXTRACTION_INPUT_SCHEMA,
            tracker=tracker,
            model_override=model_override,
            call_reason="session_note_extraction",
        )
    except ModelCallError as exc:
        print(
            f"[session-note-extraction] upstream extraction failed after retries and the Anthropic fallback "
            f"({exc}) -- returning an honest extraction-failure marker instead of raising."
        )
        return _extraction_failed_result(str(exc))
    return _normalize(raw)


# --- local, file-content-hash-keyed cache (agent-making-local, not a DB) ---

_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "session_note_extractions"


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _cache_path(content_hash: str) -> Path:
    return _CACHE_DIR / f"{content_hash}.json"


def _load_cached(content_hash: str) -> dict[str, dict[str, Any]] | None:
    path = _cache_path(content_hash)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None  # corrupt/unreadable cache entry -- treat as a miss, re-extract


def _save_cache(content_hash: str, result: dict[str, dict[str, Any]]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(content_hash).write_text(json.dumps(result, indent=2), encoding="utf-8")


def _read_file_text(file_path: str) -> str:
    """PDF files go through the same extract_pdf_text every other real
    document in this pipeline uses. A plain .txt file (or anything pypdf
    can't parse as a PDF) falls back to reading it as raw text -- session
    notes aren't guaranteed to arrive as PDFs the way a TP always does.
    """
    if file_path.lower().endswith(".pdf"):
        pages = extract_pdf_text(file_path)
        return "\n\n".join(f"--- Page {p['page_number']} ---\n{p['text']}" for p in pages) or "(no extractable text)"
    return Path(file_path).read_text(encoding="utf-8", errors="replace")


def extract_session_note_file(
    file_path: str,
    *,
    tracker: CallTracker,
    model_override: str | None = None,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """The one callable a future backend endpoint/button should wire
    straight into (per Round 59's explicit scope: build the function, not
    the button). Given a real file path, returns the normalized 5-field
    extraction -- from the local cache if this exact file's content has
    already been processed, otherwise makes one real model call and
    caches the result before returning it.

    Raises FileNotFoundError immediately (before any cache lookup or model
    call) if the path doesn't exist -- same convention as
    supporting_doc_extraction.py's extract_supporting_document.

    Fix Round (2026-08-27): `force_refresh=True` -- a real production
    crash, confirmed: a cache entry written before a field (note_
    detail_level, the QA-COC-01 fix round) was added to the extraction
    shape fails validation in whatever Pydantic model a caller builds
    from this function's return value, with no way to recover short of
    manually deleting the cache file. `force_refresh` skips the cache
    LOOKUP (so a stale/incompatible entry is never returned) but still
    WRITES the fresh result back to the cache afterward, same as a normal
    cache miss -- unlike `use_cache=False`, which skips saving too and
    would silently turn every future read of this same file back into a
    real model call forever. A caller that discovers its OWN validation
    of a cached read failed (e.g. a missing field) should retry with
    force_refresh=True exactly once -- this self-heals the cache for
    every read after that, rather than needing use_cache=False's
    permanent no-cache behavior or a manual cache-directory wipe.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"session note file not found: {file_path}")

    content = path.read_bytes()
    content_hash = _content_hash(content)

    if use_cache and not force_refresh:
        cached = _load_cached(content_hash)
        if cached is not None:
            print(f"[session-note-extraction] cache hit for {path.name} ({content_hash[:12]}...) -- no model call made")
            return cached

    full_text = _read_file_text(file_path)
    result = extract_session_note_text(full_text, tracker=tracker, model_override=model_override)

    # Live incident fix (2026-08): a genuine extraction-failure marker
    # (EXTRACTION_ERROR_KEY present -- see extract_session_note_text's own
    # docstring) is deliberately NOT cached -- this is this file's bad
    # luck on THIS run, not a fact about the file's own content. The next
    # real attempt at this same file (a retried upload, a page refresh)
    # gets a fresh real try, not a frozen failure baked into the cache.
    if use_cache and EXTRACTION_ERROR_KEY not in result:
        _save_cache(content_hash, result)
    return result
