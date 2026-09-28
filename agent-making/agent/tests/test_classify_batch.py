from pathlib import Path

from ..pipeline.api import classify_batch_pdf

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
