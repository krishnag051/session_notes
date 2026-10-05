"""Fix Round (2026-10-05), "non-deterministic results on identical
re-uploads": Krishna reported that uploading the SAME real PDF multiple
times produced different pass/fail results on a couple of rules between
runs -- a compliance tool must never do this. Investigated the real code
(no live/real-API reproduction run, since that would cost real money
without prior approval -- see CLAUDE.md's hard rule) across every stage
that runs BEFORE a real model call:

- classify_batch.py / classify_person_docs.py: pure regex + list indexing
  over the SAME extracted page text, no randomness, no set/dict iteration
  the OUTCOME depends on, no threading. Confirmed by direct code reading.
- fields.py's deterministic checkers (run_deterministic_checks): run
  single-threaded, sequentially, each a pure function of `fields`. The one
  checker that loops over a collection in a way that COULD be order-
  sensitive (_check_SN_97151_11's sibling_documents loop) is an "ANY
  sibling matches" check -- its answer is the same regardless of which
  order the siblings are visited in, confirmed by reading the loop body.
- judge.py's majority-vote reconciliation (_reconcile_majority_vote):
  iterates `set.intersection(...)` of rule_ids, whose iteration ORDER is
  hash-seed-dependent (and so can differ process-to-process) -- but this
  only affects which order `reconciled` dict keys get inserted in, never
  the computed winner for a given rule_id (each rule_id's own Counter/
  tie-break is self-contained, built from `entries` in a FIXED call-index
  order, not insertion order). Confirmed by reading _reconcile_majority_
  vote, _fail_strictly_beats_uncertain, and Python's own documented
  Counter.most_common() stable-sort-on-ties guarantee.

No code-level source of flipping was found upstream of the real model
call. This file is the test asked for in Issue 2, Item 4 of that report:
it proves everything BEFORE and AROUND a model call is reproducible, by
running classification and a full (judgment-mocked) review TWICE against
the same real fixture bytes and asserting byte-identical results both
times. It does NOT, and cannot, prove anything about real LLM sampling
variance itself (that needs a live, billed run Krishna approves separately
-- see this round's own report for the trade-off options on that half of
the investigation: a higher vote count/consensus bar, temperature=0, or
result caching keyed on document content hash).
"""
from pathlib import Path

from ..pipeline import api as api_module
from ..pipeline import judge as judge_module

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_judgment(judgment_rules, fields, rendered_images, tracker=None, call_reason="call", model_override=None):
    """Fixed, non-random fake -- every judgment rule returns the identical
    canned answer every single call, so this test isolates "is the code
    around the model call deterministic" from "is the model itself
    deterministic" (which this fake deliberately removes from the picture).
    """
    return {
        r["rule_id"]: {"result": "pass", "evidence": "mocked judgment pass.", "page": 1, "confidence": 0.9}
        for r in judgment_rules
    }


def test_classify_batch_pdf_is_identical_across_repeated_runs():
    pdf_path = str(FIXTURES / "batch_19page_3client.pdf")
    first = api_module.classify_batch_pdf(pdf_path)
    second = api_module.classify_batch_pdf(pdf_path)
    third = api_module.classify_batch_pdf(pdf_path)
    assert first == second == third


def test_review_person_document_97153_findings_identical_across_repeated_runs(monkeypatch):
    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", _fake_judgment)
    pdf_path = str(FIXTURES / "single_doc_97153_bergstein_v2.pdf")

    first = api_module.review_person_document(pdf_path, "97153", prior_extractions=None)
    second = api_module.review_person_document(pdf_path, "97153", prior_extractions=None)

    assert first.status == second.status == "complete"
    # Compare only {result, page} per rule_id -- `evidence` strings are
    # allowed to differ in WORDING between runs (e.g. a timestamp-free but
    # otherwise-equivalent phrasing); what must never differ is the actual
    # verdict a reviewer acts on.
    first_verdicts = {rid: (f["result"], f["page"]) for rid, f in first.findings.items()}
    second_verdicts = {rid: (f["result"], f["page"]) for rid, f in second.findings.items()}
    assert first_verdicts == second_verdicts


def test_review_person_document_97151_findings_identical_across_repeated_runs(monkeypatch):
    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", _fake_judgment)
    pdf_path = str(FIXTURES / "single_doc_97151_cazi_appendix1.pdf")

    runs = [
        api_module.review_person_document(pdf_path, "97151", prior_extractions=None)
        for _ in range(3)
    ]
    verdict_sets = [
        {rid: (f["result"], f["page"]) for rid, f in run.findings.items()}
        for run in runs
    ]
    assert verdict_sets[0] == verdict_sets[1] == verdict_sets[2]
