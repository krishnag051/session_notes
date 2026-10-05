# Project Preparation — Session Note Compliance Tool

> Personal interview-prep notes. Facts are drawn directly from the codebase,
> commit history, and this project's own working session log. Anything not
> directly confirmed is marked **needs confirmation** rather than guessed.

---

## What the project does and the business problem it solves

An automated compliance auditing tool for **ABA (Applied Behavior
Analysis) therapy session notes**. ABA providers must document each
therapy session to a strict set of insurance/regulatory rules (session
duration matches the billed code, required participants were present and
recorded, the narrative has enough real detail for the length of the
session, the timesheet matches the note, etc.). Checking this by hand,
document by document, is slow and inconsistent.

This tool takes a real provider export — a single batch PDF that can
contain session notes for **many different patients in one file** — and:
1. Automatically splits the batch into per-patient documents (no manual
   pre-sorting).
2. Runs **~65 compliance rules** against each patient's own document.
3. Returns a pass/fail/uncertain/not-applicable verdict per rule, with a
   plain-English explanation, never a silent final verdict — every result
   is designed for a human reviewer to confirm or override.

## Who uses it

A single real compliance reviewer (the project owner) auditing real ABA
session-note batches for an organization ("MasterFaster," per the
account/branding used throughout the app). Whether MasterFaster is itself
the ABA provider or a third party auditing other providers' documentation
— **needs confirmation**; not stated anywhere in the project itself.

No multi-tenant customer base, no other named clients — this is a single
real reviewer's working tool, verified against real patient documents he
supplied himself.

## Complete workflow and data flow

```
Upload (PDF)
  → Classification (zero-cost, deterministic): split one batch PDF into
    per-patient page ranges, using each patient's own "Activity Statement"
    cover page as the boundary marker
  → Per patient-document:
      → Zero-cost structural extraction (provider/BCBA name, times, full text)
      → Deterministic rule checks (regex/structural: duration math,
        signature dates, attendance checkboxes, timesheet alignment,
        sibling-document cross-checks)
      → Judgment-layer rule checks (LLM, for anything a deterministic
        check can't resolve) — 5 independent calls, majority vote
      → Merge deterministic + judgment results into one findings set
      → Humanization pass (a second, separate LLM call per finding) —
        rewrites the raw/robotic reasoning into a short, plain-English
        explanation a non-technical reviewer can read directly
      → Store: raw model output (write-once, audit trail) + the
        human-reviewable version (overridable)
  → Reviewer views results, can override any individual rule's verdict
    (with a required reason, full audit log) and mark the document reviewed
  → CSV export of the full rule-by-rule findings for manual cross-audit
```

## Architecture and major components

Three independently-deployable pieces:

- **Frontend** — a Vite SPA (React), file-based routing via
  `@tanstack/router-plugin`. Talks to the backend purely over HTTP/JSON.
  No login system — a single shared view for every list/history page.
- **Backend** — FastAPI + SQLAlchemy + Alembic, Postgres in production
  (SQLite for the test suite). Owns all persistence, the audit log, human
  overrides, spend-cap enforcement, and background review orchestration.
- **agent-making** — a standalone, **stateless** rule-checking pipeline
  (plain Python, imported by path into the backend — not a separate
  service, not called over HTTP). It never fetches its own data; the
  backend hands it everything it needs (document text, this patient's
  prior documents, timesheet rows, sibling documents) as plain arguments.
  This boundary is deliberate and enforced: exactly one backend file
  (`app/agent_client.py`) is allowed to import from it.

Background work: a batch upload triggers review of every document in it
one at a time, via a FastAPI background task, **after** the upload's own
HTTP response is already sent — the frontend polls for completion rather
than blocking the request.

## Tech stack and why

| Technology | Why |
|---|---|
| **FastAPI** | Async-friendly, typed request/response models (Pydantic), fast to build a real REST API with background tasks built in. |
| **SQLAlchemy + Alembic** | Portable ORM + real, reviewable migrations — schema changes ship as versioned, auto-applied-on-boot migration files, not manual DDL. |
| **Postgres** (prod) / **SQLite** (tests) | Real relational integrity (foreign keys, no hard deletes) for audit-sensitive healthcare data; SQLite lets the full test suite run with zero external dependencies. |
| **Vite + React + TanStack Router** | File-based routing, fast dev iteration, typed route params/search (used directly for a real feature — scoping the Audits list to one upload via a URL search param). |
| **Docker + Docker Compose** | Two independent stacks (staging builds locally; production pulls prebuilt images) — lets real-data verification happen against a disposable, isolated environment before anything touches production. |
| **Anthropic Claude (Sonnet 5 + Haiku 4.5)** | Sonnet 5 for the actual judgment-layer compliance reasoning (needs real reasoning capability); Haiku 4.5 for humanization (a much simpler rewrite task — using the expensive model there would be wasted spend). |
| **OpenRouter** | A free/cheap model path, used only for development/testing variants, never the production default. |
| **pytest** | Both the backend and the rule-checking pipeline have their own full test suites, including a structural guardrail (below) that makes it impossible for a normal test run to ever spend real money. |

## My exact contribution and ownership

**Honest framing:** this system was built using **Claude Code, an AI
coding agent**, across a long, iterative working session. I (the project
owner) did not hand-type most of the implementation. What I actually did:

- Specified every requirement, often very precisely (exact rule wording
  decisions, exact spend ceilings, exact UX for a new feature).
- **Found every real bug in this project** by manually auditing real
  output against real source PDFs — not by reading code. Several of the
  most serious bugs (the cross-patient data leak, the LLM refusal-text
  leak, the sentence-count extraction gap) were found this way, with
  verbatim evidence from real documents, not caught by any automated test.
- Made the real engineering/product tradeoffs: the exact per-document
  spend ceiling, which direction an ambiguous rule should resolve,
  deliberately deferring a riskier fix (don't-send-to-the-model-at-all)
  in favor of a narrower one, approving or declining real API spend
  explicitly before every live run.
- Reviewed every round of fixes against real data myself before accepting
  it as done, across six-plus rounds of "fix → re-verify against a real
  batch → report back what's still broken."
- Directed and approved every deployment to the staging environment.

**What the AI agent did:** wrote the actual code, root-caused bugs from
tracebacks/logs when asked, wrote the test suites, wrote the migrations,
and executed the Docker deployments — under my direction and subject to
my own re-verification against real data every round.

If asked directly: *"I used an AI coding agent as my implementation tool,
the same way I'd use a pair programmer — I owned the requirements,
architecture decisions, bug-finding (via real-data audits), and
acceptance; it owned the typing."*

## AI/LLM/agentic approach, and why

Two distinct, separately-tuned LLM call sites, never conflated:

1. **Judgment layer** (Claude Sonnet 5) — for compliance questions a
   deterministic regex/structural check genuinely cannot answer (e.g.
   "is the narrative detailed enough for a session this long?"). Sampling
   variance on a real judgment call is real, not theoretical on this
   model, so a single call is never trusted alone — **5 independent calls
   are made and majority-voted (4-of-5 agreement required)**; anything
   that doesn't reach that bar is reported honestly as `uncertain` rather
   than guessed.
2. **Humanization layer** (Claude Haiku 4.5) — a second, separate real
   call per finding, run only *after* the full review already has its own
   raw reasoning for every rule. Takes the raw/robotic finding text and
   rewrites it into one short, plain-English sentence a non-technical
   reviewer can read directly. Deliberately a cheaper/faster model than
   judgment, since it's a rewrite task, not reasoning.

A real, narrow exception to pure majority-vote: when the only split is
"fail" vs. "uncertain" (never "pass") and fail is the strict plurality,
fail wins — an "uncertain" vote is a judge declining to commit, not
evidence *for* passing.

## Integrations

- **Anthropic API** — the real, billed model provider (Sonnet 5 +
  Haiku 4.5).
- **OpenRouter** — a secondary, free-tier model path for dev/testing only.
- **No MCP servers, no external SaaS integrations, no third-party APIs**
  are part of the shipped product itself.
- **No authentication/identity provider** — explicitly no login system.

## Deterministic logic vs. LLM/AI reasoning

This is a deliberate, load-bearing split, not an afterthought:

- **Deterministic, zero-cost, zero-variance** (plain Python/regex): which
  pages belong to which patient, session duration math, signature-date
  math, attendance-checkbox matching, timesheet-row matching, sentence
  counting, keyword scoping (e.g. correctly ignoring a keyword match
  inside a goal-description template rather than the real narrative).
- **LLM-based**: only the compliance questions that genuinely require
  reading comprehension/judgment, and the final human-readable rewrite.

Roughly 33 of the 65 rules resolve deterministically; the rest escalate to
the judgment layer. (Exact current split — see `rules.json`'s own
`check_type_counts`.)

## Evaluation/testing and how correctness was verified

Two layers, deliberately kept separate:

1. **Automated test suites** (pytest) for both the backend and the
   rule-checking pipeline, run against real sample PDFs with the model
   boundary mocked. A **structural guardrail** (an autouse pytest fixture)
   makes it impossible for any test to accidentally make a real, billed
   API call — it patches every real call site to raise immediately unless
   a test is explicitly marked `@pytest.mark.real_api`, and even that
   marker is never self-granted permission; a human approves the exact
   command, call count, and cost estimate every time. This is the single
   engineering decision I'd call out as most interesting: it turns "don't
   accidentally spend money in CI" from a policy into something the test
   suite itself enforces.
2. **Real-data verification** — every round of fixes was re-run against
   real patient documents in a disposable staging deployment, with a hard
   per-document spend ceiling, and manually audited line-by-line against
   the real source PDFs before being accepted as correct. This is how
   every real bug in this project was actually found — the automated
   tests confirmed logic didn't regress; they did not find the bugs
   themselves.

## Production deployment and infrastructure

- Two separate Docker Compose stacks: **staging** (builds from local
  checkout, its own ports/volumes/secrets, safe to throw away) and
  **production** (pulls prebuilt images from Docker Hub — **not yet
  pushed/deployed for real as of this writing** — needs confirmation on
  final go-live date).
- Database migrations (Alembic) run automatically on every container
  start (`alembic upgrade head` baked into the image's own startup
  command) — idempotent, no manual migration step in normal operation.
- No seed-data step — the compliance rules themselves ship baked into the
  backend image at build time.
- Postgres is never published on the host in production (SSH-tunnel only)
  — a deliberate, stated security decision, not an oversight.

## Monitoring, logging, error handling and recovery

- Every real API call site logs its own call count and running cost
  estimate.
- A document's review failing with an unexpected exception gets a bounded
  number of automatic retries with backoff before landing in a permanent,
  clearly-logged `failed` state — never a silent infinite stall.
- A crash anywhere in the post-review storage step rolls back any
  partially-written rows and marks the review `failed` with the full
  traceback, rather than leaving it stuck at `processing` forever with no
  visible error (this exact failure mode happened once in practice — see
  below).
- A hard, enforced **per-document spend ceiling** spans all three real
  call stages (extraction + judgment + humanization) combined — each
  stage gets only its own remaining budget, so the total structurally
  cannot exceed the ceiling regardless of which stage would otherwise
  spend the most. A second, independent ceiling caps an entire batch's
  cumulative spend.
- **No dedicated external monitoring/alerting (e.g. Sentry, Datadog) is
  wired in** — needs confirmation if this is planned; today, errors are
  visible via logs and the review's own stored `error_message` field.

## Real-world problems/failures encountered, and how they were solved

- **Cross-patient data leak**: a "does this person have a related document
  elsewhere in this same upload?" lookup filtered only by which batch a
  document came from, not which *patient* it belonged to — since one
  batch can hold many different patients, a patient with no real sibling
  document of their own could be handed *another real patient's* document
  content as if it were theirs. Fixed by scoping strictly to the same
  patient ID; added a regression test using a real multi-patient fixture.
- **A 10-minute apparent "hang"**: none of the real Anthropic client
  constructions set an explicit timeout, so they ran on the SDK's own
  10-minute default. Once a feature started making many more real calls
  per document, one slow/rate-limited call could make a whole review look
  stuck. Fixed with explicit, short, bounded timeouts everywhere.
- **A silent background-task crash masquerading as a hang**: the actual
  root cause of the above — a tuple-shape mismatch between two functions
  (one returned a 2-tuple, the caller expected a 3-tuple) crashed
  mid-process, and that crash had nowhere to surface inside a background
  task, leaving the review stuck with no visible error at all. Fixed the
  shape bug and added structured failure handling so any future crash in
  that code path fails loudly instead of silently.
- **LLM refusal-text leaking into user-facing output**: twice, for two
  different underlying reasons, the humanization model responded with its
  own first-person clarification/refusal ("I need the original text to
  rewrite...") instead of a rewrite, and that text got stored and shown to
  the reviewer as if it were a real compliance finding. Fixed with a
  rule-agnostic refusal detector (falls back to the original raw text)
  plus a prompt redesign (worked examples instead of bare imperative
  rules, which turned out to be more failure-prone).
- **Narrative-extraction corruption from page-break headers**: a
  multi-page document reprints its own header on every page; when a
  section happened to start right at a page boundary, the extraction
  logic mistook the reprinted header for the end of the real content,
  producing wildly inconsistent, sometimes near-empty extracted text.
  Fixed by stripping repeated page headers before extraction.
- **Unexplained data loss (environmental, not fully root-caused)**: at one
  point several database tables were unexpectedly empty, coinciding with
  Postgres restart-cycle log entries consistent with a host sleep/Docker
  Desktop restart. Reported honestly as unresolved rather than guessed at
  — **needs confirmation/follow-up** if this recurs.

## Key performance/business results and measurable impact

No formally tracked business metrics (hours saved, dollar impact, adoption
numbers) exist in this project as documented — **needs confirmation**
before claiming any of these in an interview. What's concretely true and
sourced from the project itself:

- ~65 real compliance rules checked per document, automatically.
- A single batch upload can classify and review **many patients' worth of
  documents from one file**, with zero manual pre-sorting.
- Real per-document cost for a full automated review (extraction +
  judgment + humanization combined) has run at roughly **$0.10–$0.40 per
  document** in real verification runs, now hard-capped at **$2.00 per
  document** regardless.
- Multiple real, production-shaped bugs were found and fixed through
  structured, repeatable real-data verification rounds (six-plus distinct
  rounds of fix → re-verify against real documents → report).

## Security, authentication, permissions and data considerations

- **No authentication system** — a deliberate, stated decision, not an
  oversight (the UI says so directly: "No login required"). Every list
  view is a single shared view across anyone using the app.
- **No hard deletes anywhere** — records are flagged (`active=false`),
  never deleted, except one explicit, separately-confirmed "delete
  forever" action.
- **Full field-level audit log** — every mutating action writes a
  before/after diff in the same transaction as the change.
- **Human override is paramount**: every consumer of a rule's result reads
  the human-overridable value, never the model's own raw output directly
  — the model's original output is preserved separately and is never
  edited after the fact, so there's always a clean distinction between
  "what the AI said" and "what the human reviewer decided."
- Handles real patient data (names, dates of birth, session details) —
  **this is healthcare-adjacent compliance data; no formal HIPAA
  compliance review/BAA is documented as part of this project — needs
  confirmation** before describing it as HIPAA-compliant in an interview.

## Important technical decisions and why

- **agent-making kept stateless and import-boundary-restricted** — makes
  the rule-checking logic testable and reusable independent of the
  backend's own database/schema, and means exactly one file would need to
  change if that pipeline's interface ever did.
- **Human override as a first-class, separate axis from the model's own
  output** — a compliance tool that silently let a human edit overwrite
  the AI's original answer would destroy the audit trail; keeping both
  values, always, was a deliberate design invariant from day one.
- **Real-data verification over trusting automated tests alone** — every
  bug that actually shipped was found by manually re-reading real
  documents against real output, not by unit tests; the automated suite's
  job was regression prevention, not bug discovery.
- **Per-document hard spend cap, enforced by construction** — rather than
  a single global cap checked loosely, each real call stage is handed only
  its own remaining budget, so the cap cannot be exceeded by any
  combination of stages, by design, not by hoping each stage behaves.

## What makes this technically interesting/difficult

- Reconciling genuine LLM non-determinism (sampling variance on judgment
  calls) with a compliance product that needs a defensible, explainable
  verdict — solved with majority voting plus an honest `uncertain` state,
  rather than ever presenting a guess as a confident answer.
- A real, open-ended failure mode (an LLM responding with meta-commentary
  instead of doing the task) that can't be fully pattern-matched away —
  this is an ongoing, acknowledged limitation, not a solved problem.
- Diagnosing a production "hang" that was actually two independent causes
  stacked together (a real SDK timeout risk, and a separate silent crash)
  — required real traceback evidence, not guessing from symptoms alone.
- Keeping a hard real-money spend ceiling correctly enforced across three
  independently-implemented call stages without ever letting any one of
  them exceed its own fair share.

## Limitations / things still incomplete

- No automated tracking of hours/cost saved vs. manual review — **needs
  confirmation** if this is wanted.
- The refusal-detector safety net is pattern-based, not a semantic
  guarantee — a new LLM wording could still slip through on some future
  input shape.
- `audit_pass_threshold` (the % score that separates a "pass" from a
  "fail") is an unconfirmed guess (80%), not a verified real business
  number.
- Production has not yet actually been deployed/pushed live as of this
  writing — **needs confirmation** on go-live status.
- No external monitoring/alerting service wired in.
- No formal HIPAA/compliance sign-off documented.

---

## Interview Q&A

**Q: What does this project do, in one sentence?**
A: It automatically audits real ABA therapy session notes against ~65
compliance rules, splitting a multi-patient batch PDF into per-patient
documents and giving a human reviewer a pass/fail per rule with a
plain-English explanation they can override.

**Q: Why not just use one LLM call to check everything?**
A: Two reasons. First, a lot of these checks are pure math/structure
(duration, dates, checkboxes) — using an LLM for those would be slower,
more expensive, and less reliable than a regex. Second, for the questions
that genuinely need judgment, a single LLM call has real sampling
variance, so I vote across 5 independent calls and require 4-of-5
agreement before trusting the result.

**Q: Did you write all the code yourself?**
A: I used an AI coding agent (Claude Code) as my implementation tool. I
owned the requirements, the architecture and policy decisions, and —
critically — I found every real bug in this system myself by manually
auditing real output against real source documents. The agent wrote the
code and tests under my direction, and I reviewed and re-verified every
round against real data before accepting it.

**Q: What was the hardest bug to track down?**
A: A review that appeared to hang for over 10 minutes. It turned out to
be two separate issues: Anthropic's client SDK defaults to a 10-minute
timeout with no explicit override anywhere in the codebase, *and*
separately, a tuple-shape mismatch was crashing a background task
silently with no error surfaced anywhere. Both needed fixing — the
timeout was a real risk, but the actual crash was the real root cause
that round.

**Q: How do you know the AI's compliance verdicts are trustworthy?**
A: I don't trust any single verdict blindly — that's the whole design.
Deterministic checks have zero variance. Judgment calls are voted 5 ways
and only trusted at 4-of-5 agreement; anything less reports honestly as
"uncertain," never a guessed answer. And a human reviewer can override
any individual result, with the model's own original answer always kept
separately for audit.

**Q: What would you do differently if you started over?**
A: I'd build the refusal-detection safety net (catching the LLM
responding with meta-commentary instead of doing the task) earlier and
more generally, rather than discovering it twice on two different rules.
It's an inherently open-ended problem, and I'd rather have designed for
it up front than patched it reactively.

**Q: Is this in production today?**
A: It's been fully verified in a staging environment against real data,
multiple rounds. Final production push — needs confirmation on exact
status as of today.

---

## 30-second explanation

"I built an automated compliance auditor for ABA therapy session notes.
It takes a real provider's batch PDF — which can have many different
patients in one file — automatically figures out which pages belong to
which patient, and checks each one against about 65 real compliance
rules, using a mix of deterministic logic and an LLM judgment layer with
majority voting, so nothing is a guessed answer. A human reviewer gets a
plain-English explanation per rule and can override anything, with a full
audit trail."

## 1-minute explanation

"The problem: ABA providers have to document every therapy session
against a strict set of compliance rules, and checking that by hand,
document by document, across multi-patient batch files, is slow and
inconsistent. I built a tool that automatically splits a batch PDF into
per-patient documents, then runs around 65 rules against each one —
simple structural checks with plain code, and harder judgment questions
with an LLM, voted across 5 independent calls so a single bad sample
can't produce a wrong verdict. A second LLM pass turns the raw findings
into plain English for a non-technical reviewer, who can override any
individual result with a reason, fully logged. I used an AI coding agent
to implement it, but I owned every requirement, found every real bug by
auditing real documents by hand, and verified every fix against real
data before accepting it — across several rounds of real production-
shaped issues, from a cross-patient data leak to a silent background
crash that looked like a hang."

## 2-minute technical explanation

"Architecturally, it's three pieces: a Vite/React frontend, a FastAPI/
Postgres backend, and a standalone, stateless rule-checking pipeline that
the backend imports by path rather than calling over HTTP — deliberately
boundaried so exactly one file owns that integration.

The pipeline: a batch upload is classified with zero model calls — pure
structural logic finds each patient's own cover page and splits the batch
accordingly, flagging anything it can't confidently resolve rather than
guessing. Each patient's document then goes through deterministic checks
— duration math, signature dates, timesheet alignment, cross-checks
against a sibling document from the same upload — and anything that needs
real reading comprehension escalates to a judgment layer: Claude Sonnet 5,
called 5 times independently and majority-voted, 4-of-5 required to trust
a result, otherwise it's honestly reported as uncertain. After the full
review, a second, separate Haiku call per finding rewrites the raw
reasoning into something a non-technical reviewer can read directly — run
only after the fact, never inline, with per-finding failure isolation so
one bad rewrite can't lose an entire batch's worth of already-paid-for
work.

On reliability: there's a hard, enforced spend ceiling per document,
split across all three real call stages so none of them alone can blow
the budget; automatic retries for transient failures; and structured
failure handling so a crash anywhere fails loudly with a real error
message instead of silently leaving a document stuck mid-review — which
is exactly what happened in production once, and is why that handling
exists now.

On testing: the automated suite runs against real sample documents with
the model boundary mocked, and a structural pytest guardrail makes it
impossible to accidentally spend real money in a test run — but the
automated suite didn't find the real bugs; manual, line-by-line audits of
real output against real source PDFs did, across multiple verification
rounds, including a cross-patient data leak and an LLM refusal-text leak
that needed a rule-agnostic detection pass to fix properly.

Deployment is Docker Compose, two stacks — staging builds locally for
safe verification, production pulls prebuilt images — with Alembic
migrations running automatically on container start."

---

## Important numbers

- **~65** compliance rules checked per document.
- **5-way** independent judgment vote; **4-of-5** agreement required to
  trust a result.
- **$2.00** hard, enforced spend ceiling per document (combined across
  extraction + judgment + humanization).
- **$4.00** secondary cap per batch and per rule-engine call, independent
  of the per-document ceiling.
- Real observed per-document cost in verification: **roughly $0.10–$0.40**.
- **2 real Anthropic models** used: Claude Sonnet 5 (judgment), Claude
  Haiku 4.5 (humanization).
- **2 Docker Compose stacks** (staging, production); migrations run
  automatically on every container start.
- **Six-plus** distinct real-data verification rounds, each surfacing at
  least one genuine production-shaped bug.
- **0** authenticated users — no login system exists, by design.
- **0** hard deletes — every record is soft-flagged, with a full
  field-level audit log on every change.

## Technologies to explain

- **FastAPI** — async Python API framework; be ready to explain
  `BackgroundTasks` (how the background review job is triggered after the
  HTTP response is sent) and Pydantic request/response models.
- **SQLAlchemy + Alembic** — ORM + migrations; be ready to explain why
  migrations auto-run on container start and why that's safe (idempotent).
- **Postgres vs. SQLite** — same schema, different dialect; why the test
  suite doesn't need a real Postgres server.
- **Vite + TanStack Router** — file-based routing, build-time route
  generation, typed URL search params (used for a real filtering feature).
- **Docker Compose** (two stacks) — be ready to explain the staging-vs-
  production split and why production pulls images instead of building.
- **Anthropic Claude API** — two different models for two different jobs,
  majority voting, explicit timeouts, and why.
- **pytest** — the structural real-API guardrail specifically; this is a
  genuinely good interview talking point about defensive engineering.

## Difficult follow-up questions

**"If you didn't write the code, what did you actually do?"**
A: I specified exact requirements (down to precise rule wording and exact
dollar spend ceilings), found every real bug by manually auditing real
patient documents against real output — not something any AI or test
suite did for me — made the actual engineering tradeoff calls, and
reviewed and accepted or rejected every round of changes against real
data before it shipped. That's product ownership and technical direction,
not just prompting.

**"How do you know you actually understand the architecture if an AI
wrote it?"**
A: Because I can explain exactly why each piece exists — the stateless
pipeline boundary, the majority-vote threshold, the spend-cap design —
and because I was the one who root-caused *which* layer was responsible
when something broke (e.g. distinguishing "this is a timeout risk" from
"this is actually a crash" required reading the real traceback, not just
accepting a first guess).

**"What's a bug the AI introduced that you caught?"**
A: The cross-patient data leak — a sibling-document lookup that filtered
by upload batch but not by patient. I found it by reading a real
document's output and recognizing the reasoning text belonged to a
different real patient entirely, not by code review.

**"Is this actually in production, or just a demo?"**
A: It's been verified against real data in a dedicated staging
environment across multiple rounds, with real spend and real documents.
Final production go-live status — needs confirmation as of today.

**"What would break this at 10x the volume?"**
A: The per-document review is already one-at-a-time within a batch by
design (to keep spend predictable), so throughput, not correctness, is
the first thing I'd expect to need work — needs confirmation/further
investigation, this hasn't been load-tested.

---

## Resume-ready summary

- Directed and verified the build of an AI-assisted ABA therapy
  compliance auditing tool (FastAPI/Postgres backend, React/Vite
  frontend, standalone rule-checking pipeline) that automatically splits
  multi-patient batch PDFs and checks ~65 compliance rules per document.
- Designed an LLM judgment layer using 5-way majority voting (Claude
  Sonnet 5, 4-of-5 agreement threshold) to produce defensible, explainable
  compliance verdicts instead of single-sample guesses, with a human-
  overridable result and full audit trail on every change.
- Found and resolved multiple real production-shaped failures — including
  a cross-patient data leak, an SDK timeout masking a silent background-
  task crash, and an LLM refusal-text leak — through structured, repeated
  manual verification against real source documents, not automated
  testing alone.
- Designed and enforced a hard, per-document real-spend ceiling split
  across three independent API call stages, guaranteeing by construction
  that no combination of stages could exceed the budget.
- Built a structural test-suite safeguard (an autouse pytest guardrail)
  that makes it impossible for an automated test run to accidentally
  trigger real, billed API spend.
