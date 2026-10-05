"""Fix Round (2026-10-05), "non-determinism fix": Krishna reported that
uploading the literal same PDF multiple times produced different pass/fail
results on a couple of rules between runs. Investigation (see this round's
own report) found no code-level non-determinism anywhere upstream of the
real judgment-layer model call — the flips are real, expected LLM sampling
variance on rules that sit near a genuine judgment boundary. Decision:
guarantee "same document -> same stored verdict" directly, by reusing a
prior run's real result instead of trying to make the model itself more
consistent (a higher vote count only makes repeat-flips RARER, never
guaranteed-identical).

"Same document" is defined on EXTRACTED/CLASSIFIED CONTENT, not raw
uploaded PDF bytes — see compute_cache_key's own docstring for exactly why,
including the one real edge case this choice protects against (two uploads
of "the same" PDF that don't extract to byte-identical text).
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_client import pipeline_version_fingerprint
from app.db.models import ReviewCache


def compute_cache_key(
    *,
    full_text: str,
    service_code: str,
    prior_extractions: list[dict] | None,
    timesheet_rows: list[dict] | None,
    appendix: str | None,
    sibling_documents: list[dict] | None,
) -> str:
    """sha256 hex of a canonical JSON encoding of every stateless input
    that review_person_document actually consumes and that can change its
    result — i.e. everything EXCEPT the pdf_path itself (a temp file path
    is never meaningful to hash) and model_override/max_spend_usd (runtime
    knobs, not document identity).

    Keyed on `full_text` (this document's own extracted text), NOT the
    original batch PDF's raw bytes or a hash of the whole uploaded file.
    This is deliberate, not incidental:

    - The actual review pipeline's real input IS text — classify_batch_pdf
      and extract_pdf_full_text are both confirmed deterministic for
      byte-identical PDF input (see agent-making/agent/tests/
      test_review_repeatability.py, added this same round) — so for a
      literal re-upload of the identical file, hashing full_text and
      hashing the raw bytes would always agree in practice.
    - They are NOT guaranteed to agree in the one real edge case this
      matters for: two uploads of "the same" document that are not
      actually byte-identical (a re-scan, a re-export from a different
      tool, a different PDF producer/version embedding slightly different
      text) could extract to subtly different full_text. Hashing full_text
      directly means THAT case correctly produces a cache MISS (a fresh,
      real review), rather than a hash-of-bytes approach that could either
      false-negative (treat two genuinely-identical-looking documents as
      different because of incidental byte differences — header metadata,
      PDF producer tag — that never reached the extracted text at all,
      costing an unnecessary real re-review) or, worse, never arise here
      at all because it was keyed on the wrong layer to catch a real
      content difference. Hashing the layer the pipeline actually
      consumes is both the more correct and the more conservative choice.

    Does NOT fold in `pipeline_version_fingerprint()` — that is tracked as
    its OWN separate column on ReviewCache (see that model's own
    docstring) so a lookup can filter on cache_key AND pipeline_version
    independently, and so an old row under a prior pipeline_version is
    never deleted, just never matched again.
    """
    canonical = json.dumps(
        {
            "full_text": full_text,
            "service_code": service_code,
            "prior_extractions": prior_extractions or [],
            "timesheet_rows": timesheet_rows or [],
            "appendix": appendix,
            "sibling_documents": sibling_documents or [],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lookup(db: Session, *, cache_key: str, pipeline_version: str | None = None) -> ReviewCache | None:
    """Returns the live cached row for this exact (cache_key, pipeline_version)
    pair, or None on a miss — a miss covers both "never reviewed before"
    and "reviewed before, but under an older rules/prompt version," which
    is exactly the distinction this round's fix requires: the OLD row
    under the old pipeline_version is left untouched (no hard deletes,
    same as everywhere else in this schema) but is never matched here
    again. `pipeline_version=None` (the default) resolves the CURRENT
    fingerprint — tests that need to simulate "an old cached row under a
    pipeline version that no longer exists" pass an explicit value instead.
    """
    pipeline_version = pipeline_version if pipeline_version is not None else pipeline_version_fingerprint()
    return db.execute(
        select(ReviewCache).where(
            ReviewCache.cache_key == cache_key, ReviewCache.pipeline_version == pipeline_version,
        )
    ).scalar_one_or_none()


def record_hit(db: Session, cache_row: ReviewCache) -> None:
    cache_row.hit_count += 1
    db.add(cache_row)


def upsert(db: Session, *, cache_key: str, pipeline_version: str | None, payload: dict) -> ReviewCache:
    """Writes (or REPLACES, in place — same row, not a second insert) the
    live cached answer for (cache_key, pipeline_version). Called after
    every successful real review (automatic or forced) — a `force_rerun`
    becomes the new cached answer for future automatic lookups too, per
    the explicit decision in this round's own report (flagged there in
    case a different default is wanted: an ephemeral forced re-run that
    never updates the cache is an equally defensible choice, just not the
    one implemented).
    """
    pipeline_version = pipeline_version if pipeline_version is not None else pipeline_version_fingerprint()
    existing = db.execute(
        select(ReviewCache).where(
            ReviewCache.cache_key == cache_key, ReviewCache.pipeline_version == pipeline_version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.payload = payload
        db.add(existing)
        return existing
    row = ReviewCache(cache_key=cache_key, pipeline_version=pipeline_version, payload=payload, hit_count=0)
    db.add(row)
    return row
