# Session Note Compliance Tool

Automated ABA session-note compliance auditing, with mandatory human review.
Frontend (React/Vite SPA) in `/frontend`, backend (not yet built) in
`/backend`, the standalone rule-checking pipeline (not yet built) in
`/agent-making`.

**Read `docs/backend-agent-making-plan.md` first** — the agreed architecture:
what's lifted near-unchanged from the prior TP-review project's pipeline
(deterministic checks → judgment/majority-vote → integrity-retry → merge →
humanize), what's genuinely new for this project (batch classification/
splitting, persistent cross-upload person identity), the backend data model,
and the build order.

## 🛑 HARD RULE (2026-09-27, permanent, effective immediately) — NO REAL API CALLS WITHOUT EXPLICIT PER-INSTANCE PERMISSION

> Hard rule, effective immediately and permanent: no real API calls without my
> explicit, per-instance permission.
>
> Never run anything that makes a real call to the Anthropic API — not a live
> session-note review, not a "real content" test file, not a diagnostic probe,
> not a background verification pass — without stopping first and telling me
> exactly what you want to run and why, including your best estimate of how
> many calls/how much it will cost. Wait for explicit approval before running
> it. Default to reporting code as complete-but-unverified-against-real-API
> rather than spending money to verify it without asking. Mock/synthetic
> tests, linters, and anything that costs nothing stay exactly as encouraged
> as before — this rule is only about real Anthropic API spend, not about
> testing rigor in general.

This applies everywhere in this repo — `frontend/`, `backend/`, and
`agent-making/` alike.

**Structural guardrail — status: ACTIVE (2026-09-27), enforced in
`agent-making/agent/tests/conftest.py`.** Not just prose: an `autouse`
pytest fixture patches every real call site this pipeline has to
Anthropic/OpenRouter (`judge.anthropic.Anthropic`, `model_provider.
_call_openrouter`, `model_provider._call_anthropic`, `humanize.
humanize_evidence_with_llm`) so each one raises before any HTTP request is
ever constructed — for every test in the suite, by default. The one escape
hatch is `@pytest.mark.real_api`, which never self-grants permission — a
human still approves the exact command, call count, and cost estimate every
time. A hard per-session real-call ceiling (`MAX_REAL_API_CALLS_PER_SESSION`,
default 4) applies once a test **is** approved, so an approved test still
can't quietly make more real calls than agreed. Proof this actually works:
`agent-making/agent/tests/test_real_api_guardrail.py`.

There is no `backend/` test suite yet — the moment one exists, wire the
identical mechanism into `backend/tests/conftest.py` too, before writing any
test that touches the backend's own import of `agent-making`'s review
wrapper. Do not treat "backend doesn't exist yet" as a reason to skip this —
it means there is nothing to guard yet there, not that the guard can wait
once there is.

## Invariants — never violate these, ever, regardless of what a task seems to ask for

These are drafted from the architecture already agreed in
`docs/backend-agent-making-plan.md`; wording may need adjusting once the real
schema exists, but the substance is decided, not tentative:

- **Human override is paramount.** Every consumer of a rule result reads
  `final_status` / `final_finding` / `final_pages` on `rule_results` — never
  the model's raw `model_status` / `model_finding` / `model_pages`. The
  model_* columns are written once by the pipeline and never updated again,
  by anyone, for any reason.
- **A `PersonDocument`'s classification (which pages belong to which
  person/appendix) is inspectable and correctable independently of rule
  results.** A wrong split (silently merging two people, or dropping a real
  note) is a worse failure mode than a wrong rule verdict — flag when the
  classifier is unsure, don't guess.
- **Review Status and Audit Flags are two independent fields, never
  collapsed into one.** `reviewed` / `reviewed_by` / `reviewed_at` is a
  separate axis from `audit_result` (pass/fail) — a note can be
  Failed-and-Reviewed, Failed-and-Not-Reviewed, Passed-and-Not-Reviewed, etc.
  Already established this way in the frontend (`frontend/src/routes/audits.index.tsx`'s
  "Audit Flags" vs. "Review Status" columns).
- **No hard deletes.** Flag (`voided`, `active=false`), never `DELETE` a row
  that represents something that happened.
- **Every mutating endpoint writes an audit_log entry** — a field-level diff,
  in the same transaction as the change, via one shared helper, never ad hoc
  elsewhere.
- **`Person.global_key` (name+DOB, normalized) is derived, never manually
  typed.** Don't add a UI path that lets a reviewer hand-edit it into
  colliding with another real person; a correction to a misclassified person
  goes through an explicit "merge/split person" operation with its own audit
  trail, not a raw field edit.

## When something in a task conflicts with an invariant above

Stop and say so — don't silently pick a side. This is a healthcare-compliance
product; the invariants exist for specific, deliberate reasons. If a request
seems to require breaking one, that's a signal to ask, not to route around it
quietly.
