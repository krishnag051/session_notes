from pathlib import Path

from ..pipeline.api import classify_batch_pdf
from ..pipeline.classify_batch import classify_batch

FIXTURES = Path(__file__).parent / "fixtures"


def _by_name(result: dict, name: str) -> dict:
    return next(p for p in result["people"] if p["full_name"] == name)


def test_1client_holland_daylyn():
    result = classify_batch_pdf(str(FIXTURES / "holland_daylyn_1client.pdf"))

    assert len(result["people"]) == 1
    assert result["admin_noise_pages"] == []
    assert result["unresolved"] == []

    person = result["people"][0]
    assert person["full_name"] == "Daylyn Holland"
    assert person["dob"] == "2018-12-11"
    assert person["global_key"] == "holland_daylyn_20181211"
    assert person["cover_page"] == 1
    assert [d["appendix"] for d in person["documents"]] == ["I", "II", "III"]
    assert [d["service_code"] for d in person["documents"]] == ["97151", "97151", "97151"]
    assert [d["date_of_service"] for d in person["documents"]] == [
        "2026-09-04", "2026-09-05", "2026-09-06",
    ]


def test_19page_3client_subset():
    result = classify_batch_pdf(str(FIXTURES / "batch_19page_3client.pdf"))

    assert result["unresolved"] == []
    assert len(result["admin_noise_pages"]) == 1
    assert len(result["people"]) == 3

    names = {p["full_name"] for p in result["people"]}
    assert names == {"Joseph Bergstein", "Daniel Cazi", "Shimon Drummer"}

    cazi = _by_name(result, "Daniel Cazi")
    assert cazi["global_key"] == "cazi_daniel_20180723"
    assert [d["service_code"] for d in cazi["documents"]] == ["97151", "97156"]
    assert [d["appendix"] for d in cazi["documents"]] == ["I", "II"]

    bergstein = _by_name(result, "Joseph Bergstein")
    assert len(bergstein["documents"]) == 1
    assert bergstein["documents"][0]["service_code"] == "97153"
    assert bergstein["documents"][0]["page_start"] == 2
    assert bergstein["documents"][0]["page_end"] == 6

    admin = result["admin_noise_pages"][0]
    assert admin["page_start"] == 13
    assert admin["page_end"] == 13


def test_72page_14client_full_batch():
    result = classify_batch_pdf(str(FIXTURES / "batch_72page_14client.pdf"))

    assert result["unresolved"] == []
    assert len(result["admin_noise_pages"]) == 1
    assert len(result["people"]) == 13

    names = [p["full_name"] for p in result["people"]]
    assert names == [
        "Joseph Bergstein", "Daniel Cazi", "Shimon Drummer", "Zeldy Ekstein",
        "Nachmen Falkowitz", "Shea Freund", "Charny Gluck", "Shaya Herskovic",
        "Shea Herskovic", "Aiza Nabiha", "Udy Reichman", "Lazer Schnitzer",
        "Solomon Schnitzer",
    ]

    # Every person's document page ranges must exactly tile that person's
    # page range with no gaps or overlaps (the classifier's own internal
    # consistency check, independent of any hand-counted expectation).
    for person in result["people"]:
        docs = person["documents"]
        assert docs, f"{person['full_name']} resolved with zero documents"
        expected_start = docs[0]["page_start"]
        for doc in docs:
            assert doc["page_start"] == expected_start
            expected_start = doc["page_end"] + 1

    # The one deliberate near-collision in this fixture: two different
    # people sharing a DOB must still resolve to two distinct global_keys.
    shaya = _by_name(result, "Shaya Herskovic")
    shea = _by_name(result, "Shea Herskovic")
    assert shaya["dob"] == shea["dob"] == "2022-06-16"
    assert shaya["global_key"] != shea["global_key"]

    admin = result["admin_noise_pages"][0]
    assert admin["page_start"] == 13
    assert admin["page_end"] == 13

    # Every page in the source document must be accounted for exactly once,
    # across people + admin_noise (unresolved is empty here) — no page
    # silently dropped, no page double-counted into two buckets.
    accounted_pages = set()
    for person in result["people"]:
        accounted_pages.add(person["cover_page"])
        for doc in person["documents"]:
            accounted_pages.update(range(doc["page_start"], doc["page_end"] + 1))
    for noise in result["admin_noise_pages"]:
        accounted_pages.update(range(noise["page_start"], noise["page_end"] + 1))
    assert accounted_pages == set(range(1, 73))


def _page(number: int, text: str, low_text: bool = False) -> dict:
    return {"page_number": number, "text": text, "low_text": low_text}


def test_invoice_cover_page_format_real_client_bug():
    """Regression for a real bug (2026-10-15): a real client's own 961-page
    export used "Invoice - <name>" cover pages instead of "Activity
    Statement - <name>", so _COVER_RE matched nothing and the whole file
    fell into one `unresolved` blob. Mirrors the real fixture's own text
    (page 1 of agent-making/sample_pages_dump.txt, captured directly from
    that real client file) rather than a synthetic shape.
    """
    pages = [
        _page(1, (
            "Invoice \n"
            "Invoice - SAFEER ABDULLAH \n"
            "Billed To:\n"
            "10/08/2026 Aminul H Hridoy 97153: RBT/BT Adaptive Behavior Treatment by Protocol 12: Home 8.00 $600.00\n"
            "Notes: see Appendix I.\n"
        )),
        _page(2, (
            "Behavior Technician Direct Care Session Note\n"
            "Patient Name: SAFEER ABDULLAH  Provider Name: Aminul H Hridoy, BT\n"
            "Patient DOB: 04/25/2019\n"
            "Session Date: 10/08/2026 Service Code: 97153\n"
            "Page 1 of 1 I - Page 1 of 1\n"
        )),
    ]

    result = classify_batch(pages)

    assert result["unresolved"] == []
    assert result["admin_noise_pages"] == []
    assert len(result["people"]) == 1

    person = result["people"][0]
    assert person["full_name"] == "SAFEER ABDULLAH"
    assert person["dob"] == "2019-04-25"
    assert person["cover_page"] == 1
    assert len(person["documents"]) == 1


def test_haiku_fallback_only_fires_on_the_section_the_regex_path_could_not_resolve(monkeypatch):
    """Regression for the 2026-10-15 Haiku 5.5 fallback: it must fire ONLY
    for a section the deterministic regex path already failed on (here,
    "Bravo" is missing its 'Patient DOB:' field), never for a section the
    regex path resolves cleanly on its own ("Alpha"). Mocks classify_batch's
    own call_tool_json import -- no real network/API call possible from
    this test regardless (conftest.py's autouse guardrail blocks the
    underlying seam too), but mocking at this higher level also lets the
    test assert the real call COUNT and the real prompt content.
    """
    import agent.pipeline.classify_batch as classify_batch_module

    pages = [
        _page(1, "Invoice - ALPHA PATIENT \nNotes: see Appendix I.\n"),
        _page(2, (
            "Patient Name: ALPHA PATIENT\nPatient DOB: 01/01/2020\n"
            "Session Date: 01/02/2026 Service Code: 97151\nI - Page 1 of 1\n"
        )),
        _page(3, "Invoice - BRAVO PATIENT \nNotes: see Appendix I.\n"),
        _page(4, (
            "Patient Name: BRAVO PATIENT\n"  # deliberately no 'Patient DOB:' field
            "Session Date: 01/03/2026 Service Code: 97153\nI - Page 1 of 1\n"
        )),
    ]

    calls = []

    def _fake_call_tool_json(*, prompt_text, tracker, **kwargs):
        calls.append(prompt_text)
        tracker.record(reason="classification_fallback", provider="anthropic", model="claude-haiku-5-5",
                        usage={"input_tokens": 500, "output_tokens": 100})
        return {
            "resolvable": True,
            "full_name": "Bravo Patient",
            "dob": "02/02/2020",
            "documents": [{
                "service_code": "97153", "date_of_service": "01/03/2026", "page_start": 3, "page_end": 4,
            }],
        }

    monkeypatch.setattr(classify_batch_module, "call_tool_json", _fake_call_tool_json)

    result = classify_batch_module.classify_batch(pages, batch_label="unit-test-batch")

    assert len(calls) == 1
    assert "BRAVO PATIENT" in calls[0]
    assert "ALPHA PATIENT" not in calls[0]

    assert result["unresolved"] == []
    names = {p["full_name"] for p in result["people"]}
    assert names == {"ALPHA PATIENT", "Bravo Patient"}

    bravo = next(p for p in result["people"] if p["full_name"] == "Bravo Patient")
    assert bravo["resolved_via"] == "haiku_5_5_fallback"
    assert bravo["documents"][0]["service_code"] == "97153"


def test_haiku_fallback_result_failing_sanity_check_stays_unresolved(monkeypatch):
    """A fallback result that doesn't pass the real sanity checks (here: a
    page range outside the section's own bounds) must be discarded, not
    trusted -- the section stays in `unresolved`, same as if the fallback
    had never been attempted.
    """
    import agent.pipeline.classify_batch as classify_batch_module

    pages = [
        _page(1, "Invoice - CHARLIE PATIENT \nNotes: see Appendix I.\n"),
        _page(2, "Patient Name: CHARLIE PATIENT\nSession Date: 01/04/2026 Service Code: 97151\nI - Page 1 of 1\n"),
    ]

    def _fake_call_tool_json(*, tracker, **kwargs):
        tracker.record(reason="classification_fallback", provider="anthropic", model="claude-haiku-5-5",
                        usage={"input_tokens": 100, "output_tokens": 50})
        return {
            "resolvable": True,
            "full_name": "Charlie Patient",
            "dob": "03/03/2020",
            "documents": [{
                "service_code": "97151", "date_of_service": "01/04/2026",
                "page_start": 1, "page_end": 99,  # out of this section's real bounds (1-2)
            }],
        }

    monkeypatch.setattr(classify_batch_module, "call_tool_json", _fake_call_tool_json)

    result = classify_batch_module.classify_batch(pages, batch_label="unit-test-batch")

    assert result["people"] == []
    assert len(result["unresolved"]) == 1
    assert "Patient DOB" in result["unresolved"][0]["note"]
