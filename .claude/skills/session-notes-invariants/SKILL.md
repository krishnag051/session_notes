---
name: session-notes-invariants
description: Consult this whenever writing or editing backend code that touches session_note_reviews, rule_results, person, or person_document — specifically: marking a review reviewed, overriding a rule result, merging/splitting Person records, or re-running a misclassified batch split. Also consult when writing tests for any of the above. Covers the exact transactional order, guard conditions, and audit-write pattern each of these operations requires, once decided — these are easy to get subtly wrong by writing "reasonable-looking" code that skips a guard or writes audit entries outside the transaction.
---

# Session Notes — Mutation Conventions

This project is a healthcare-compliance tool. Every mutation below has (or
will have, once designed) a specific, deliberate order — don't reorder steps
or drop a guard because the happy path "looks like it would work" without it.
Sections below marked **PLACEHOLDER — not yet designed** describe nothing
real yet; don't invent behavior for them. Fill them in from actual decisions
as they're made, the same discipline the prior TP-review project's
`tp-review-invariants` skill followed — this file should only ever describe
what's actually been decided.

## The audit-write pattern (applies to every mutation in this file, once built)

Every operation below must write its audit_log row **inside the same DB
transaction** as the data change, using one shared helper (not yet built —
name/location TBD once the backend exists), the same shape as:

```python
record(
    session,                    # same session/transaction as the mutation
    user_id=current_user.id,    # None only for scheduled/system jobs
    action="human-readable summary, e.g. 'Marked SNR-2026-0500 reviewed'",
    target_type="session_note_review",
    target_id=review.id,
    details={"reviewed": {"from": False, "to": True}},  # field-level diff, changed fields only
)
```

Never construct an audit_log row directly elsewhere. Never write it in a
separate transaction/commit after the fact — if the transaction rolls back,
the audit entry must roll back with it.

## Mark Reviewed (PLACEHOLDER — not yet designed)

Decided so far: `reviewed` / `reviewed_by` / `reviewed_at` on
`SessionNoteReview` is a separate axis from `audit_result`, and setting it
auto-populates the reviewer's identity from the logged-in user (see
`frontend/src/routes/audits.$auditId.tsx`'s `CURRENT_USER` wiring — a real
backend endpoint replaces that mock write). Not yet decided: whether marking
reviewed is reversible, whether it's blocked on `audit_result` being present
first, or what (if anything) it's blocked on.

## Override a rule result (PLACEHOLDER — not yet designed)

Decided so far: consumers always read `final_status` / `final_finding` /
`final_pages`, never `model_status` / `model_finding` / `model_pages` (see
CLAUDE.md's invariants). Not yet decided: whether overrides are draft-only
like the prior project's (blocked once some terminal state is reached), the
exact guard order, or the optimistic-locking scheme.

## Merge / split a Person record (PLACEHOLDER — not yet designed)

Decided so far: `Person.global_key` is derived (normalized name+DOB), never
hand-typed, and a misclassified person must be corrected through an explicit
merge/split operation with its own audit trail — never a raw field edit (see
CLAUDE.md). Not yet decided: the merge/split endpoint shape, what happens to
already-completed `SessionNoteReview` rows attached to the person(s) involved,
or how history-comparison rules behave across a merge.

## Re-run a misclassified batch split (PLACEHOLDER — not yet designed)

Decided so far: a `PersonDocument`'s classification is inspectable and
correctable independently of rule results (see CLAUDE.md) — a natural home
for a "flag this batch's split for manual review" escape hatch, per
`docs/backend-agent-making-plan.md` §4. Not yet decided: whether correcting a
split re-triggers rule-checking on the affected `SessionNoteReview`(s), or
what happens to a review already completed against the wrong split.

## Writing tests for any of the above

Once a section above moves from PLACEHOLDER to decided, add its own
"guards, checked in this order" writeup (same shape as the prior project's
`tp-review-invariants` skill) before writing tests against it — the tests
should assert the exact order, not just the end state.
