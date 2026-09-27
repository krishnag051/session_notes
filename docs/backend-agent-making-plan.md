# Session-Note Tool — Backend & Agent-Making Plan

Based on a full read-through of the old TP project's real, built pipeline (`agent-making/agent/pipeline/`), its backend wrapper (`backend/app/rule_engine/client.py`, `app/agent_client.py`), its Docker setup, and its `Patient`/`Version`/`Upload` data model.

## 1. What the old project's pipeline actually does (confirmed by reading the code, not the docs)

Three layers, exactly as `final-documents/01_System_Architecture.md` describes, but the real mechanism went further than that doc says:

- **Deterministic checkers** (`fields.py`) — regex/structural/arithmetic checks, zero cost, zero variance.
- **Judgment layer** (`judge.py`) — one real Claude call per batch of judgment rules, forced through a tool-use schema (`FINDINGS_TOOL`) so every rule_id comes back as `{result, evidence, page, confidence, evidence_supports_result}`.
- **Self-consistency / voting, in its current, real form** (this is more evolved than the architecture doc states):
  - `run_judgment_checks` — 2 calls; if they agree, done; if they disagree on specific rule_ids, ONE additional batched 3rd call covers just the disagreeing subset, then 2-of-3 majority wins, else "uncertain."
  - `run_judgment_checks_majority_vote(n_calls, min_agreement)` — the version actually wired into production now: 5 concurrent calls (`ThreadPoolExecutor`), needs 4-of-5 agreement or falls back to uncertain. This replaced the 2-call version after real measurement showed single-call sampling variance is high even with `temperature` unavailable on this model.
  - `STABILIZED_UNCERTAIN_RULE_IDS` — a deliberate escape hatch: rule_ids confirmed to coin-flip even under voting are pulled out of the judgment call entirely and given a fixed, zero-cost "needs human review" finding instead of ever being asked.
- **`integrity.py`** — retries a judgment batch when a rule_id goes missing from the response (dropped, or rejected because `evidence_supports_result=False`), and does a separate, targeted "page recovery" pass for findings that came back without a page citation.
- **`call_tracker.py`** (`ApiCallTracker`) — every real call, wherever it originates, is counted against a shared cap (`max_calls`) and produces real `usage`/cost numbers. This is the single choke point that makes "no unbounded spend" enforceable in code, not just in a CLAUDE.md rule.
- **`merge.py`** — reconciles deterministic vs. judgment findings per rule (deterministic wins unless escalated; judgment wins on escalation but deterministic's own page is preferred when it has one) and explodes multi-page findings into export rows.
- **`humanize.py`** — turns internal vocabulary ("2-of-3 majority", raw result tokens) into reviewer-facing plain English. This mattered enough that the old project has ~4 rounds of fixes just for this.
- **`model_provider.py`** — free OpenRouter tier as the default for anything that isn't a real production review (session-note field extraction, dev/test), real Anthropic only for the actual paid review path, with automatic OpenRouter→Anthropic fallback on a gateway failure. `call_tool_json` is the shared low-level "get me structured JSON back from a model, retried" primitive.
- **Session notes are already partially built here.** `session_note_extraction.py` extracts 6 fields (session_date, session_location, clinician/patient telehealth location, assessment_activity, note_detail_level) from a single note file, cached by SHA-256 content hash so a re-run of the same file never re-calls the model. `session_note_comparison.py` (not fully read here, but referenced throughout `rule_engine/client.py`) cross-references those extracted fields against the TP's own stated fields for 3 specific "compound" rules (QA-COC-01, QA-ACF-12, plus QA-OBS-03's simpler presence check) — this is the closest existing analog to what the new tool needs to do full-time, but today it's a small side-channel feeding into a TP review, not the main product.

**Bottom line: the deterministic + judgment(self-consistency/majority-vote) + integrity-retry + call-tracker + merge + humanize architecture is generically reusable almost as-is.** None of it assumes anything about treatment plans specifically — it operates on "a set of rules, extracted page text/images, here's your findings schema." That whole stack should be lifted into the new project's `agent-making/` largely unchanged in shape (new rule set, new prompts, same machinery).

## 2. What's structurally different, and why (confirmed, not assumed)

This is the real gap between the two projects, and it's exactly what you flagged verbally — now confirmed against the actual old `Patient`/`Version` model:

```python
class Patient:
    id: uuid
    reference_id: str   # unique, but MANUALLY entered by a human, per-record
    name: str
    ...
class Version:            # one row per TP revision for that ALREADY-KNOWN patient
    patient_id -> Patient
    version_number
    ...
```

The old project's patient identity is **not auto-detected at all** — a reviewer explicitly picks or creates the patient record before ever uploading a file, and every upload after that belongs to a version chain the reviewer already navigated to. There is no classification step anywhere in the old pipeline that looks at a raw PDF and asks "whose document is this, and have I seen this person before?" It never needed one: one TP, one known patient, one upload flow.

The new session-note tool needs exactly that missing piece, because its whole premise is a person dropping in a **batch PDF containing many clients' notes at once**, with no per-client pre-selection. That means two genuinely new subsystems that have no analog to lift from the old project:

1. **A classification/splitting stage** — given N pages, find the cover-page boundaries ("Activity Statement - [Name]"), assign each page range to a person, detect and drop admin-noise cover pages with no attached note, and — within one person's pages — split further by Appendix number when multiple service codes are bundled under one cover page. This is pure structural/deterministic work (footer "Page X of Y" parsing, "Appendix I/II" text matching) — closer in spirit to `fields.py`'s deterministic checkers than to anything judgment-based, and should probably stay 100% deterministic for the same reason the old project prefers deterministic checks wherever possible: zero cost, zero variance, and a wrong split is a much worse failure mode (silently merges two people, or drops a real note) than a wrong rule verdict.
2. **A persistent, cross-upload person identity** — `name + DOB` (normalized), not a human-assigned `reference_id`. Every new batch upload must resolve each split-out person against this existing table (fuzzy-safe on name normalization, but exact on DOB) rather than creating a fresh unconnected record — this is what makes the "compare this note against this same person's prior notes" rules (copy-paste/identical-data-point detection) possible at all, and it's a real, permanent difference in the data model, not just an added column.

## 3. Proposed `agent-making/` structure for the new project

Mirror the old project's separation exactly (standalone, importable-by-path, never HTTP, called only through thin wrapper functions — same reasoning as the old `INTEGRATION_PLAN.md`'s locked boundary):

```
agent-making/
  agent/
    pipeline/
      extract.py            # reuse pattern: PDF -> per-page text (+ low-text flags)
      classify_batch.py      # NEW: cover-page boundary detection -> list of PersonDocumentSet
      classify_person_docs.py# NEW: within one person's pages -> {cover_page, appendix_docs[]}
      fields.py              # deterministic checkers, adapted to session-note rule set
      judge.py                # LIFT the self-consistency/majority-vote machinery near-unchanged
      integrity.py             # LIFT unchanged (missing-rule retry + page-recovery pass)
      call_tracker.py           # LIFT unchanged
      merge.py                  # LIFT, adapt to session-note findings shape
      humanize.py                # LIFT, adapt phrasing
      model_provider.py           # LIFT unchanged (OpenRouter default + Anthropic fallback)
      session_note_extraction.py   # LIFT near-unchanged — already does per-note field extraction
      history_comparison.py         # NEW: given a person's ID, pull their prior stored notes
                                     #      and check copy-paste / identical-data-point rules
      api.py                          # NEW public wrapper, this project's pipeline/api.py analog:
                                       #   def review_session_note_batch(pdf_path, person_history_lookup) -> BatchReviewResult
    rules/
      rules.json               # the ~22-ish session-note questions, same shape as the old rules.json
    tests/                     # same discipline: mocked model boundary, no real API spend without approval
```

`api.py`'s wrapper is the one new piece of interface design needed: unlike the old project's `review_treatment_plan(pdf_path)` (one document, one patient, no history), this one needs to either (a) accept a callback/lookup function the backend provides for "give me this person's last N stored notes" so agent-making never touches the database directly (keeping the same "agent-making is standalone, backend calls in" boundary), or (b) accept the prior notes' already-extracted-field JSON directly as a parameter, which is cleaner and keeps agent-making fully stateless — the backend is already storing every note's `session_note_extraction.py` output per person, so it can hand over "here are this person's last 5 extractions" as plain data rather than a live callback. Recommend (b): stateless, testable without a real DB, consistent with how `extra_rule_context` already works in the old project.

## 4. Backend data model — the real new pieces

```
Person
  id (uuid)                       # internal PK
  global_key (text, unique)       # normalized "lastname_firstname_YYYYMMDD"
  full_name (text)
  dob (date)
  created_at

SessionNoteBatch                  # one row per multi-client PDF upload
  id, uploaded_by, uploaded_at, original_filename, page_count, status

PersonDocument                    # one row per split-out person-within-a-batch
  id, batch_id -> SessionNoteBatch, person_id -> Person (nullable until resolved)
  cover_page_range, note_page_ranges (jsonb: [{appendix, service_code, page_start, page_end}])
  is_admin_noise (bool)           # true = cover page with no real note, excluded from history

SessionNoteReview                 # one row per actual reviewed document (per appendix/service code)
  id, person_document_id -> PersonDocument, person_id -> Person
  service_code, date_of_service
  status (processing/ready/error) # same job-status shape as the old project's uploads.status
  score, audit_result
  reviewed (bool), reviewed_by, reviewed_at    # SEPARATE from audit_result — same real distinction
                                                 # your screenshot review already flagged as missing
  created_at

RuleResult                        # same shape as the old project's, FK'd to SessionNoteReview instead of Upload
  rule_id, model_status, model_finding, model_pages, final_status, final_finding, ...
```

`Person.global_key` replaces the old project's manually-typed `reference_id` — it's derived, not entered, and it's the join key every new batch upload resolves against before creating a new `SessionNoteReview`. `PersonDocument` is the new project's real structural novelty: it's the record of *what the classifier decided*, kept separate from *what the rule-checker concluded*, so a classification mistake is inspectable/correctable independently of rule results — this also gives you a natural home for a "flag this batch's split for manual review" escape hatch if the classifier is ever unsure.

Everything else — `RuleResult`'s `model_status`/`final_status` split, human-override-is-paramount, audit_log on every mutation, no hard deletes — carries over as invariants worth keeping, they're generically good practice for a compliance tool, not TP-specific.

## 5. Processing flow (batch → per-person parallelism → dashboard)

1. **Upload** — batch PDF lands, `SessionNoteBatch` row created, status `processing`.
2. **Classify (fast, deterministic, single pass)** — `classify_batch.py` splits into `PersonDocument` rows immediately; each resolves against `Person.global_key` (match existing or create new); admin-noise cover pages get flagged and excluded. This step alone can complete and be visible in the UI within seconds, independent of any rule-checking — directly solves the "15-minute wait with no visibility" complaint pattern from the old project, because the person list can render before a single rule check has run.
3. **Rule-check, per person, in parallel** — same `ThreadPoolExecutor` pattern `judge.py`'s `run_judgment_checks_majority_vote` already uses internally, one level up: a bounded pool (4-5 concurrent workers, matching what you asked for) each processing one `PersonDocument`'s note(s) end-to-end (extract → deterministic → judgment/vote → merge → history comparison against that person's prior stored extractions). Each `SessionNoteReview` flips `processing` → `ready` independently and streams into the dashboard/Audits list as it finishes, rather than the whole batch gating on its slowest member.
4. **History write-back** — once a review completes, its `session_note_extraction.py` output is stored against `Person.id` (not just this one review) specifically so the *next* batch that includes this same person automatically has their history available for the copy-paste/identical-data-point rules, with zero new external data source needed — exactly the mechanism you worked out earlier.
5. **Dashboard** surfaces `SessionNoteReview` rows across all people/batches, sorted newest-first, same Review Status vs. Audit Flags split as the real Brellium UI.

## 6. Docker / deployment — directly reusable pattern

The old project's compose setup is a genuinely good template, reuse it close to verbatim:

- **One combined backend image**, not two services — agent-making is copied into the image as a plain importable-by-path Python package (`sys.path.insert`), never run as its own HTTP service. Same reasoning applies here: no network hop, no serialization boundary to version, and it keeps the "agent-making is standalone, backend calls via thin wrapper" rule enforceable by import structure, not just convention.
- **Backend Dockerfile** builds from the **repo root** as context (not `./backend` alone), so it can `COPY agent-making/agent/pipeline` and `COPY agent-making/agent/rules/rules.json` the same way — copy only pipeline/rules.json into the image, never tests/sample data/archives.
- **A named cache volume for the extraction cache** (`session_note_extraction.py`'s content-hash cache dir) — same reasoning as the old project's `tp_session_note_cache` volume: without it, a container restart silently re-triggers billed calls for files already processed. This one probably matters *more* here since every session note (not just TPs) goes through this extraction.
- **Frontend Dockerfile** — confirmed by reading it directly: the old project's frontend is **already** a Vite SPA using `@tanstack/router-plugin` for build-time, file-based routing — not the full TanStack Start SSR framework. It builds to a plain static `dist/` (`index.html` + `assets/`), served by nginx with an SPA fallback. This means the "TanStack → plain Vite" conversion you're describing for the Lovable export is very likely **just**: moving `index.html` to the project root (Lovable's TanStack Start template often nests it), confirming `vite.config.ts` uses `@tanstack/router-plugin`'s Vite plugin (not the SSR `@tanstack/start` plugin), and confirming `package.json`'s build script is a plain `vite build`, not a server-bundle build. If the Lovable export is genuinely using `@tanstack/start` (SSR) rather than just the router plugin, that's a bigger swap (drop the SSR plugin/entry files, add a plain `main.tsx` + `App.tsx` root render) — worth checking which one it actually is before assuming the small fix is enough. The old project's `frontend/Dockerfile`, `vite.config.ts`, and `nginx.conf` are ready-made references for exactly this.
- **staging vs. production split** — same two-compose-file pattern (`docker-compose.yml` pulls prebuilt Docker Hub images for prod, `docker-compose.staging.yml` builds fresh locally with a `-p` project namespace so volumes never collide) is worth copying directly; it's not TP-specific at all.
- Per your standing rule, all `docker` commands here are still run by you, not executed by me.

## 7. Cost discipline — carry the same hard rules forward

The old project's `CLAUDE.md` hard rule (no real Anthropic API calls without explicit per-instance approval, an autouse pytest fixture that structurally blocks real calls in tests by default, a hard per-session real-call ceiling with a `@pytest.mark.real_api` escape hatch) is exactly the right template for the new project too, especially since the new tool's majority-vote judgment layer (4-5 calls per person, per note) multiplies real spend faster than the old project's original 2-call design did. Recommend setting this up in the new repo's `CLAUDE.md`/`AGENT_STATE.md` from day one rather than retrofitting it after a real accidental spend, the way the old project's own history shows it had to.

## 8. Suggested build order

1. Classification/splitting (`classify_batch.py` + `classify_person_docs.py`) — pure deterministic, testable against your 3 real sample PDFs (1-client, 19-page, 72-page) with zero API cost. This is the new project's own foundation and has no old-project analog to lean on, so it deserves to be built and hardened first.
2. Lift `judge.py`/`integrity.py`/`call_tracker.py`/`model_provider.py` into the new `agent-making/` mostly unchanged; write the new `rules.json` for the ~22 session-note questions; wire `fields.py`'s deterministic checkers for whichever of those 22 are structural/arithmetic (signature-within-2-days, blank-field detection, etc.).
3. Wire `session_note_extraction.py` in near-verbatim; build `history_comparison.py` as the one genuinely new judgment-adjacent module (copy-paste/identical-data-point checks against a person's stored prior extractions).
4. Backend: `Person`/`SessionNoteBatch`/`PersonDocument`/`SessionNoteReview` tables, the batch-upload endpoint, per-person parallel processing via a background task pool (same `BackgroundTask`-per-job shape as the old project's `run_upload_pipeline`, just fanned out per person instead of one-per-upload).
5. Frontend: convert the Lovable/TanStack export per §6 above, wire it to the real endpoints in place of mock data.
6. Docker/compose, staging first, using the old project's files as the direct template.

Steps 1-3 need zero backend/frontend work and can be developed and tested against your existing real sample PDFs entirely inside `agent-making/` first — the same "prove the pipeline standalone before wiring it in" discipline the old project's own `INTEGRATION_PLAN.md` insisted on.
