"""Mocked model boundary — confirms reconciliation logic, never a real call."""
from ..pipeline import judge as judge_module


def _finding(result, page=None):
    return {"result": result, "evidence": f"evidence for {result}", "page": page, "confidence": 0.9}


def test_reconcile_majority_vote_simple_majority_wins():
    all_results = [
        {"R-1": _finding("pass")},
        {"R-1": _finding("pass")},
        {"R-1": _finding("fail")},
    ]
    reconciled = judge_module._reconcile_majority_vote(all_results)
    assert reconciled["R-1"]["result"] == "pass"


def test_reconcile_majority_vote_falls_back_to_uncertain_below_min_agreement():
    all_results = [
        {"R-1": _finding("pass")},
        {"R-1": _finding("pass")},
        {"R-1": _finding("pass")},
        {"R-1": _finding("fail")},
        {"R-1": _finding("fail")},
    ]
    # 3/5 agree on "pass" -- a simple majority, but below a 4-of-5 bar.
    reconciled = judge_module._reconcile_majority_vote(all_results, min_agreement=4)
    assert reconciled["R-1"]["result"] == "uncertain"


def test_reconcile_majority_vote_meets_min_agreement_bar():
    all_results = [{"R-1": _finding("pass")} for _ in range(4)] + [{"R-1": _finding("fail")}]
    reconciled = judge_module._reconcile_majority_vote(all_results, min_agreement=4)
    assert reconciled["R-1"]["result"] == "pass"


def test_reconcile_majority_vote_prefers_an_entry_with_a_real_page():
    all_results = [
        {"R-1": _finding("fail", page=None)},
        {"R-1": _finding("fail", page=12)},
        {"R-1": _finding("fail", page=None)},
    ]
    reconciled = judge_module._reconcile_majority_vote(all_results, min_agreement=2)
    assert reconciled["R-1"]["page"] == 12


def test_reconcile_majority_vote_prefers_fail_over_uncertain_when_fail_is_strict_plurality():
    """Real bug (Cazi 97151/SN-97151-09): 3-of-5 judges said fail, 2 said
    uncertain -- below the 4-of-5 bar, but zero judges said pass. The old
    reconciliation discarded this real majority as a blanket "uncertain";
    an "uncertain" vote isn't evidence FOR passing, so fail (the only
    committed verdict, and the plurality) should win."""
    all_results = [
        {"R-1": _finding("fail")},
        {"R-1": _finding("fail")},
        {"R-1": _finding("fail")},
        {"R-1": _finding("uncertain")},
        {"R-1": _finding("uncertain")},
    ]
    reconciled = judge_module._reconcile_majority_vote(all_results, min_agreement=4)
    assert reconciled["R-1"]["result"] == "fail"


def test_reconcile_majority_vote_still_falls_back_to_uncertain_on_an_exact_fail_uncertain_tie():
    all_results = [{"R-1": _finding("fail")} for _ in range(2)] + [{"R-1": _finding("uncertain")} for _ in range(2)]
    reconciled = judge_module._reconcile_majority_vote(all_results, min_agreement=3)
    assert reconciled["R-1"]["result"] == "uncertain"


def test_reconcile_majority_vote_still_falls_back_to_uncertain_when_a_real_pass_vote_exists():
    """A genuine pass-vs-fail disagreement (a real question about which
    way this goes) must stay untouched -- any pass vote at all means this
    isn't the narrow "fail vs hedging" case the fix targets."""
    all_results = [
        {"R-1": _finding("fail")},
        {"R-1": _finding("fail")},
        {"R-1": _finding("uncertain")},
        {"R-1": _finding("pass")},
    ]
    reconciled = judge_module._reconcile_majority_vote(all_results, min_agreement=3)
    assert reconciled["R-1"]["result"] == "uncertain"


def test_two_way_uncertain_finding_still_uncertain_on_an_exact_tie():
    f, s = _finding("fail"), _finding("uncertain")
    result = judge_module._two_way_uncertain_finding(f, s)
    assert result["result"] == "uncertain"


def test_reconcile_majority_vote_only_reconciles_rule_ids_present_in_every_call():
    all_results = [
        {"R-1": _finding("pass"), "R-2": _finding("pass")},
        {"R-1": _finding("pass")},  # R-2 missing from this call
    ]
    reconciled = judge_module._reconcile_majority_vote(all_results, min_agreement=2)
    assert "R-2" not in reconciled
    assert "R-1" in reconciled


def test_run_judgment_checks_majority_vote_makes_exactly_n_calls(monkeypatch):
    call_reasons = []

    def _fake_once(judgment_rules, fields, rendered_images, tracker=None, call_reason="call", model_override=None):
        call_reasons.append(call_reason)
        return {"R-1": _finding("pass")}

    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", _fake_once)
    result = judge_module.run_judgment_checks_majority_vote(
        [{"rule_id": "R-1"}], fields={"pages": []}, rendered_images={}, n_calls=5, min_agreement=4,
    )
    assert len(call_reasons) == 5
    assert result["R-1"]["result"] == "pass"


def test_run_judgment_checks_majority_vote_empty_rules_makes_zero_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(judge_module, "_run_judgment_checks_once", lambda *a, **k: calls.append(1))
    result = judge_module.run_judgment_checks_majority_vote([], fields={"pages": []}, rendered_images={})
    assert result == {}
    assert calls == []


def test_stabilized_uncertain_finding_is_a_fixed_zero_cost_finding():
    finding = judge_module.stabilized_uncertain_finding("SN-97153-06")
    assert finding["result"] == "uncertain"
    assert finding["confidence"] == 0.0
    assert "human review" in finding["evidence"].lower()


def test_stabilized_uncertain_rule_ids_empty_to_start():
    # Per this phase's own scope -- no per-call sampling variance has been
    # measured yet against this project's own rules.
    assert judge_module.STABILIZED_UNCERTAIN_RULE_IDS == frozenset()


def test_findings_dict_from_list_rejects_evidence_supports_result_false():
    findings_list = [
        {"rule_id": "R-1", "result": "pass", "evidence": "ok", "page": 1, "confidence": 0.9, "evidence_supports_result": True, "nothing_relevant_found_anywhere": False},
        {"rule_id": "R-2", "result": "fail", "evidence": "contradicts itself", "page": 2, "confidence": 0.9, "evidence_supports_result": False, "nothing_relevant_found_anywhere": False},
    ]
    result = judge_module._findings_dict_from_list(findings_list)
    assert "R-1" in result
    assert "R-2" not in result


def test_findings_dict_from_list_drops_malformed_non_dict_entries():
    findings_list = [
        "a malformed string entry, not a dict",
        {"rule_id": "R-1", "result": "pass", "evidence": "ok", "page": 1, "confidence": 0.9, "evidence_supports_result": True, "nothing_relevant_found_anywhere": False},
    ]
    result = judge_module._findings_dict_from_list(findings_list)
    assert list(result) == ["R-1"]


def test_findings_dict_from_list_flags_missing_page_as_page_unresolved_not_dropped():
    findings_list = [
        {"rule_id": "R-1", "result": "fail", "evidence": "a real problem", "page": None, "confidence": 0.9, "evidence_supports_result": True, "nothing_relevant_found_anywhere": False},
    ]
    result = judge_module._findings_dict_from_list(findings_list)
    assert result["R-1"]["page_unresolved"] is True


def test_findings_dict_from_list_does_not_flag_genuine_nothing_found_anywhere():
    findings_list = [
        {"rule_id": "R-1", "result": "not_checkable", "evidence": "nothing to find", "page": None, "confidence": 0.0, "evidence_supports_result": True, "nothing_relevant_found_anywhere": True},
    ]
    result = judge_module._findings_dict_from_list(findings_list)
    assert "page_unresolved" not in result["R-1"]
