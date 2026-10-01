# Session Note Compliance Tool — Project Overview

This document describes the system as it stands today, for someone who
wasn't involved in building it. For day-to-day coding conventions and
hard rules (especially around real API spend), see `CLAUDE.md` first —
this file assumes you've read that.

## What this app does

ABA (Applied Behavior Analysis) therapy providers submit "session notes"
— documentation of a therapy session — that have to comply with a set of
insurance/regulatory rules (session duration matches the billed code,
required participants were present, narrative detail meets a minimum
density, etc.). Checking this by hand is slow and inconsistent.

This tool takes a batch PDF (a provider's own export, which can contain
session notes for **many different patients in one file**), automatically
figures out which pages belong to which patient, runs ~65 compliance
rules against each patient's own document, and gives a human reviewer a
pass/fail score per rule with a plain-English explanation — never a
final verdict with no human in the loop. A reviewer can mark a document
as reviewed and override any individual rule's result, with a full audit
trail of who changed what and why.

**Who uses it:** a compliance reviewer (today: Krishna), uploading real
batches and auditing the automated findings against their own read of
the source documents.

**What an upload produces:** one `SessionNoteBatch`, split into one
`PersonDocument` per patient found in the batch, each with its own
`SessionNoteReview` (the automated run) made up of many `RuleResult` rows
(one per compliance rule checked).

## Architecture overview

```
┌─────────────┐      HTTP (JSON, /api/*)      ┌──────────────────┐
│  Frontend    │ ───────────────────────────▶ │  Backend          │
│  (Vite SPA,  │ ◀─────────────────────────── │  (FastAPI)        │
│  TanStack    │                               │                   │
│  Router)     │                               │  - app/routers/*  │
└─────────────┘                               │  - app/services/* │
                                                │  - app/agent_client.py
                                                │    (the ONLY file  │
                                                │    allowed to import│
                                                │    agent-making)   │
                                                └─────────┬─────────┘
                                                          │ imports by path
                                                          │ (not HTTP, not a
                                                          │  separate service)
                                                          ▼
                                                ┌───────────────────┐
                                                │  agent-making/     │
                                                │  (standalone rule- │
                                                │  checking pipeline)│
                                                │  - classify_batch  │
                                                │  - fields.py       │
                                                │    (deterministic) │
                                                │  - judge.py        │
                                                │    (judgment/LLM)  │
                                                │  - merge.py        │
                                                │  - humanize.py     │
                                                └─────────┬─────────┘
                                                          │ real, billed calls
                                                          ▼
                                                ┌───────────────────┐
                                                │  Anthropic API      │
                                                │  (Claude Sonnet 5 — │
                                                │  judgment; Claude   │
                                                │  Haiku 4.5 —        │
                                                │  humanization)      │
                                                │  + OpenRouter       │
                                                │  (dev/cheap path)   │
                                                └───────────────────┘

                                                ┌───────────────────┐
         Backend  ────────────────────────────▶│  Postgres          │
                                                │  (people, batches, │
                                                │   documents,       │
                                                │   reviews,         │
                                                │   rule_results,    │
                                                │   audit_log)       │
                                                └───────────────────┘
```

**Backend** (`backend/`): FastAPI + SQLAlchemy + Alembic, talking to
Postgres. Every endpoint lives under `/api/*`. `app/agent_client.py` is
the single, deliberate boundary — it is the *only* file in the backend
allowed to import from `agent-making`, so there is exactly one place
that would need to change if the pipeline's own interface ever did.

**agent-making** (`agent-making/`): a standalone, stateless rule-checking
pipeline. It never fetches its own data — the backend hands it everything
it needs (a document's text, this person's prior documents, timesheet
rows, sibling documents) as plain arguments. This statelessness is
deliberate: agent-making has no database of its own and no concept of
"this backend's schema."

**Frontend** (`frontend/`): a Vite SPA using `@tanstack/router-plugin`
for file-based routing (routes are generated into
`src/routeTree.gen.ts` at build time — never hand-edit that file).
Talks to the backend purely over HTTP/JSON. No login system — see
`AppSidebar.tsx`'s own "No login required" note; every list/history view
is a single shared view across everyone who uploads.

**Background review:** a batch upload triggers review of every document
in that batch one at a time, in a FastAPI `BackgroundTasks` callback
(`app/services/batch_reviews.py::run_batch_reviews` →
`app/routers/person_documents.py::run_review`), *after* the upload's own
HTTP response has already been sent. The frontend polls for completion
rather than waiting on the request.

## The review pipeline, end to end

For one `PersonDocument`:

1. **Upload & classification** (`POST /api/batches`) — the whole batch
   PDF is classified with **zero model calls**: `classify_batch.py` finds
   each person's own "Activity Statement - <Name>" cover page (a real,
   structured per-session timesheet, not just a page-boundary marker),
   splits the batch into per-person page ranges, and further splits by
   Appendix number when one person has multiple service codes bundled
   together. Anything the classifier can't confidently resolve becomes
   an `unresolved` `PersonDocument` row (flagged, not guessed) rather
   than being silently merged into the wrong person.

2. **Extraction** — two more zero-cost structural passes
   (`fields.py` helpers): provider/BCBA name, session start/end time,
   full page text. Plus one *real, billed* call,
   `session_note_extraction.py` (6-field structured extraction), capped
   independently.

3. **Deterministic checks** (`fields.py`) — regex/structural rules with
   zero cost and zero variance: session duration math, signature-date
   math, attendance checkboxes, **timesheet alignment** (the Activity
   Statement — the same cover page from step 1 — is a real second data
   source now wired in for every "does this match the timesheet?" rule;
   it is never treated as "unavailable"), and **sibling-document checks**
   (a rule phrased as "at least one session note," not scoped to one
   service type, checks this *same patient's* other documents from the
   *same upload* — strictly scoped by `person_id`, see Known Limitations
   below for why that scoping mattered).

4. **Judgment layer** (`judge.py`) — for rules a deterministic check can't
   resolve: a real, billed call to Claude Sonnet 5, voted **5 times
   independently** (4-of-5 agreement required to trust the result,
   otherwise `uncertain`) — sampling variance on a genuine judgment call
   is real, not theoretical, so no single call is ever trusted alone. One
   narrow, explicit exception: when the only disagreement is "fail" vs.
   "uncertain" (never "pass"), fail wins if it's the strict plurality — an
   "uncertain" vote isn't evidence *for* passing, just a judge declining
   to commit.

5. **Merge** (`merge.py`) — combines deterministic + judgment results into
   one `findings` dict, one entry per rule.

6. **Humanization** (`humanize.py`, via `app/agent_client.py`) — a
   **separate, real Haiku call per finding**, run *after* the full review
   already has its own raw reasoning text for every rule (never inline
   during rule-checking). Replaced an earlier client-side string-
   truncation heuristic that could silently drop the actual reason a
   rule failed. Runs with bounded concurrency (8 workers), per-finding
   failure isolation (one bad response falls back to the raw text for
   *that finding only*), and a refusal-detector safety net (see Known
   Limitations).

7. **Storage** — one `RuleResult` row per rule, written once
   (`model_status`/`model_finding`/`model_finding_raw`/`model_evidence`),
   with `final_status`/`final_finding`/`final_pages` copied from those at
   creation time — every consumer reads the `final_*` columns, never
   `model_*`, so a human override (via `PATCH /rule-results/{id}`) can
   change what's shown without ever touching the model's own original
   output.

**Per-document hard spend cap:** `settings.per_document_hard_cap_usd`
(default $2.00) is enforced across *all three* real call stages
(extraction + judgment + humanization) combined for one document. Each
stage is given only its own *remaining* headroom under that ceiling as
its own spend cap — never the full $2 — so the three stages together
structurally cannot exceed it, regardless of which one would otherwise
spend the most. There's a second, independent cap
(`batch_max_spend_usd`) across an entire batch's documents, so one
runaway document can't silently eat the whole batch's budget either.

**Retries & failure handling:** an unexpected exception during a
document's review gets up to `review_retry_attempts` (default 2)
automatic retries with a short backoff, since a transient failure
(flaky response, momentary network blip) shouldn't need a human to
notice and manually re-trigger it. A review's own internal "spend cap
already hit" result is *not* retried (retrying wouldn't help). Any crash
anywhere in the post-review section (building `RuleResult` rows,
scoring) rolls back whatever was partially staged and marks the review
`failed` with the real error message — this used to be able to die
silently inside the background task, leaving a review stuck at
`processing` forever with no visible error (see Known Limitations).

## Data model

| Table | Holds |
|---|---|
| `people` | One row per real patient, matched across uploads by `global_key` (normalized name+DOB) — **derived only**, never hand-typed; a misclassification goes through an explicit merge/split, not a raw edit. |
| `session_note_batches` | One row per upload: the real uploaded filename, the saved (UUID-named) file path, the raw classification result JSON, whether auto-review was explicitly skipped for this upload. |
| `person_documents` | One row per patient *within* a batch: which pages are theirs, their service code/date of service/appendix, classification confidence, the Activity Statement cover page number, `active` (soft-delete flag, never hard-deleted). |
| `session_note_reviews` | One row per review *run* of a `PersonDocument` (a manual re-run creates a fresh row, never overwrites the old one): status, score, audit_result, real spend/call counts, the extracted full text. `reviewed`/`reviewed_by`/`reviewed_at` is a deliberately separate axis from `audit_result` — a note can be Failed-and-Reviewed, Failed-and-Not-Reviewed, etc. |
| `rule_results` | One row per rule checked in one review: the model's own original output (`model_*`, written once, never touched again) and what's actually shown (`final_*`, which a human override can change). `model_finding_raw` is the pre-humanization text, kept separately for audit/CSV export. |
| `reviewers` | A short, typed-into-once list backing the "Reviewed By" field — no real auth, just autocomplete. |
| `audit_log` | One row per changed *field*, written in the same transaction as the change — the one shared mutation-history mechanism; nothing writes an audit entry any other way. |

## Known limitations / design decisions worth flagging

- **`audit_pass_threshold` (80%) is an unconfirmed guess**, not a
  verified real threshold — flagged in `config.py` itself. Confirm the
  real number before trusting Audit Flags at face value.
- **`session_note_reviews.extraction` (the 6-field structured output) is
  real and populated**, but was deliberately *not* wired into the
  judgment prompt as extra context — it exists for the "View Full
  Extraction" page, not as an input to rule-checking itself.
- **The humanization refusal-detector is open-ended by nature.** Claude
  has twice (so far) responded to a humanization prompt with a
  first-person refusal/meta-acknowledgment ("I need the original
  text...", "I understand, I'm ready...") instead of a rewrite — once
  triggered by a bare cross-reference string, once by a name-dense
  finding combined with an added prompt rule. The detector is now
  rule-agnostic and reasonably broad, but it is pattern-matching on
  *known* refusal phrasings, not a semantic guarantee — a new wording
  could still slip through on some future input shape. Watch for it.
- **Sibling-document checks are scoped to the same batch, same
  `person_id`.** This matters because a single batch upload is *not*
  one-patient-per-batch — it can and does contain many different
  patients together. Get this filter wrong and you leak one patient's
  real document content into another patient's review (this happened
  once — see History below).
- **No real authentication.** Every list view (Audits, Upload History) is
  a single shared view. `uploaded_by`/`reviewed_by` are free-text fields
  a user types in, not an authenticated identity.
- **Two stale comments exist in the deploy files** worth cleaning up:
  `docker-compose.yml`'s frontend service comment still says the
  backend's routes aren't under an `/api` prefix and the frontend "still
  runs on mock data" — both are no longer true (see the Deployment
  section of `README.md`).
- **No seed-data step exists.** `rules.json` (the 65 compliance rules) is
  baked into the backend image at build time, not loaded from a separate
  seed command — see the Deployment README for what that means for a
  rule change.

## History of major fixes (why certain safety nets exist)

Brief, not a full changelog — enough context to understand why the
current code looks the way it does:

- **Cross-patient sibling-document leak.** The sibling-document lookup
  (for "at least one session note" rules) originally filtered only by
  `batch_id`, with no `person_id` filter — since one batch can hold many
  patients, a patient with no real sibling of their own could be handed
  *another real patient's* document content as if it were their own.
  Fixed by scoping strictly to the same `person_id`; there's a
  regression test using a real multi-patient fixture proving this can't
  recur.
- **10-minute SDK timeout.** None of this pipeline's real Anthropic
  client constructions set an explicit timeout, so they ran on the
  SDK's own default — 10 minutes, with 2 retries on top. Once the
  humanization pass added many more real calls per document, a single
  slow/rate-limited call could make an entire review appear to hang for
  10+ minutes. Fixed with explicit, bounded timeouts (30–120s depending
  on the call) on every real client construction.
- **The humanize-results tuple-unpack crash.** The actual root cause of
  that same "stuck for 10+ minutes" report: `humanize_evidence_with_llm`
  returns a 2-tuple on success; the batch wrapper's success path was
  passing that straight through instead of reshaping it into the
  3-tuple the rest of the pipeline expected — a crash that died silently
  inside a `BackgroundTasks` callback, indistinguishable from a hang.
  This is also why the background-task failure handling described above
  (loud `failed` status, full traceback, rollback of partial rows) now
  exists at all — it didn't before this was found.
- **The humanization refusal-leak saga.** First on `SN-97153-18` (a
  bare cross-reference string, "Same reasoning as SN-97151-17.", with
  nothing substantive to rewrite), then — after fixing that one input
  shape *and adding a new prompt rule* — on an unrelated rule,
  `SN-97153-01` (a name-dense finding, likely destabilized by that same
  new rule being phrased as an imperative instruction rather than a
  worked example). Both produced a refusal/meta-commentary response
  that got stored and displayed as if it were a real finding. This is
  why the refusal-detector exists, why it's deliberately rule-agnostic,
  and why prompt rules in `humanize.py`'s system prompt now favor
  worked examples over bare imperatives where possible.
