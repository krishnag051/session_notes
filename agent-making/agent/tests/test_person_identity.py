import pytest

from ..pipeline.person_identity import global_key, normalize_dob, normalize_name


def test_normalize_name_splits_last_token_as_last_name():
    assert normalize_name("Daniel Cazi") == ("cazi", "daniel")


def test_normalize_name_lowercases_and_strips_punctuation():
    assert normalize_name("O'Brien Jr.") == ("jr", "obrien")


def test_normalize_name_rejects_empty_input():
    with pytest.raises(ValueError):
        normalize_name("   ")


def test_normalize_dob_accepts_mmddyyyy():
    assert normalize_dob("07/23/2018").isoformat() == "2018-07-23"


def test_normalize_dob_accepts_iso():
    assert normalize_dob("2018-07-23").isoformat() == "2018-07-23"


def test_normalize_dob_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_dob("not a date")


def test_global_key_is_derived_and_deterministic():
    assert global_key("Daniel Cazi", "07/23/2018") == "cazi_daniel_20180723"
    # same inputs -> same key, every time
    assert global_key("Daniel Cazi", "07/23/2018") == global_key("Daniel Cazi", "07/23/2018")


def test_global_key_distinguishes_similar_names_same_dob():
    # Real edge case found in batch_72page_14client.pdf: "Shaya Herskovic" and
    # "Shea Herskovic" share a DOB but are printed as distinct names — the
    # classifier must not silently collapse them into one person.
    assert global_key("Shaya Herskovic", "06/16/2022") != global_key("Shea Herskovic", "06/16/2022")
