"""Deterministic checkers, synthetic fixtures, zero API cost."""
from pathlib import Path

from ..pipeline import fields as fields_module
from ..pipeline.extract import extract_pdf_text

FIXTURES = Path(__file__).parent / "fixtures"


def _real_fields(fixture_name: str, service_code: str = "97153") -> dict:
    pages = extract_pdf_text(str(FIXTURES / fixture_name))
    full_text = "\n\n".join(p["text"] for p in pages)
    return {"pages": pages, "full_text": full_text, "service_code": service_code}

SAMPLE_97153_TEXT = """Behavior Technician Direct Care Session Note
Patient/Provider Information
Patient Information Provider Information
Patient Name: Test Patient  AKA: N/A Provider Name: Jane Smith, BT
Patient DOB: 01/01/2020 BCBA/LBA: John Doe BCBA, LBA

Session Information
Service Code: 97153: RBT/BT Adaptive Behavior Treatment
by Protocol  Session Duration: 2:00
Session Date: 01/15/2026 Session Units: 8 units
Session Start Time: 09:00 AM (EST) Session Location: Home
Session End Time: 11:00 AM (EST)

Who Attended Session:
☑ Patient
☑ BT/RBT
☐ BCBA /LBA
☐ Parent/caregiver
☐ Peer

Was this session Supervised by the BCBA/LBA?
No

Session Narrative:
The client worked on communication goals. Progress was steady. The client also worked on motor skills. Independence increased today.

Jane Smith added a data point 80.00 to Increase functional communication for 01/15/2026.
Jane Smith added a data point 70.00 to Increase gross motor imitation for 01/15/2026.
Jane Smith added a data point 60.00 to Increase play skills for 01/15/2026.

Provider Signature, Date: 01/16/2026
Jane Smith, BT
Patient Name: Test Patient Patient DOB: 01/01/2020 Date of Service: 01/15/2026
Page 1 of 1 I - Page 1 of 1
"""


def _fields(text: str, service_code: str = "97153") -> dict:
    return {
        "pages": [{"page_number": 1, "text": text, "low_text": False}],
        "full_text": text,
        "service_code": service_code,
    }


def test_session_duration_hours():
    assert fields_module.session_duration_hours(_fields(SAMPLE_97153_TEXT)) == 2.0


def test_session_start_and_end_time():
    f = _fields(SAMPLE_97153_TEXT)
    assert fields_module.session_start_time(f) == "09:00 AM (EST)"
    assert fields_module.session_end_time(f) == "11:00 AM (EST)"


def test_session_date_and_signature_date():
    f = _fields(SAMPLE_97153_TEXT)
    assert fields_module.session_date(f).isoformat() == "2026-01-15"
    assert fields_module.signature_date(f).isoformat() == "2026-01-16"


def test_data_point_bullets_extraction():
    bullets = fields_module.data_point_bullets(_fields(SAMPLE_97153_TEXT))
    assert len(bullets) == 3
    assert bullets[0][0] == "Jane Smith"
    assert bullets[0][1] == "Increase functional communication"
    assert bullets[0][2].isoformat() == "2026-01-15"


def test_SN_97153_16_signed_within_2_days_passes():
    result = fields_module._check_SN_97153_16(_fields(SAMPLE_97153_TEXT))
    assert result["result"] == "pass"


def test_SN_97153_16_fails_when_signed_late():
    late_text = SAMPLE_97153_TEXT.replace("Provider Signature, Date: 01/16/2026", "Provider Signature, Date: 01/25/2026")
    result = fields_module._check_SN_97153_16(_fields(late_text))
    assert result["result"] == "fail"


def test_SN_97153_17_session_no_longer_than_4_hours():
    assert fields_module._check_SN_97153_17(_fields(SAMPLE_97153_TEXT))["result"] == "pass"
    long_text = SAMPLE_97153_TEXT.replace("Session Duration: 2:00", "Session Duration: 5:00")
    assert fields_module._check_SN_97153_17(_fields(long_text))["result"] == "fail"


def test_SN_97153_19_devoid_of_nap_sleep_sickness():
    assert fields_module._check_SN_97153_19(_fields(SAMPLE_97153_TEXT))["result"] == "pass"
    sick_text = SAMPLE_97153_TEXT.replace("Progress was steady.", "Progress was steady, but the client seemed sick.")
    assert fields_module._check_SN_97153_19(_fields(sick_text))["result"] == "fail"


def test_SN_97153_21_supervised_gate_informational():
    result = fields_module._check_SN_97153_21(_fields(SAMPLE_97153_TEXT))
    assert result["result"] == "pass"  # "No" -> NOT a supervised session -> pass
    supervised_text = SAMPLE_97153_TEXT.replace(
        "Was this session Supervised by the BCBA/LBA?\nNo", "Was this session Supervised by the BCBA/LBA?\nYes",
    )
    assert fields_module._check_SN_97153_21(_fields(supervised_text))["result"] == "fail"


def test_SN_97153_05_not_applicable_when_not_supervised():
    result = fields_module._check_SN_97153_05(_fields(SAMPLE_97153_TEXT))
    assert result["result"] == "not_applicable"


def test_SN_97153_05_fails_when_supervised_and_names_mismatch():
    supervised_text = SAMPLE_97153_TEXT.replace(
        "Was this session Supervised by the BCBA/LBA?\nNo", "Was this session Supervised by the BCBA/LBA?\nYes",
    )
    result = fields_module._check_SN_97153_05(_fields(supervised_text))
    # BCBA/LBA header is "John Doe BCBA, LBA" but the signature block in this
    # fixture is Jane Smith's -- a real mismatch.
    assert result["result"] == "fail"


def test_SN_97153_05_passes_when_supervised_and_names_match():
    supervised_matching_text = SAMPLE_97153_TEXT.replace(
        "Was this session Supervised by the BCBA/LBA?\nNo", "Was this session Supervised by the BCBA/LBA?\nYes",
    ).replace("BCBA/LBA: John Doe BCBA, LBA", "BCBA/LBA: Jane Smith BCBA, LBA")
    result = fields_module._check_SN_97153_05(_fields(supervised_matching_text))
    assert result["result"] == "pass"


def test_SN_97153_22_data_point_dates_match_session_date():
    assert fields_module._check_SN_97153_22(_fields(SAMPLE_97153_TEXT))["result"] == "pass"
    mismatched_text = SAMPLE_97153_TEXT.replace(
        "Jane Smith added a data point 60.00 to Increase play skills for 01/15/2026.",
        "Jane Smith added a data point 60.00 to Increase play skills for 01/10/2026.",
    )
    assert fields_module._check_SN_97153_22(_fields(mismatched_text))["result"] == "fail"


SAMPLE_97151_ASSESSMENT_TEXT = """Assessment Session Note
Patient/Provider Information
Patient Name: Test Patient AKA: N/A Provider Name: Jane BCBA, BCBA, LBA
Patient DOB: 01/01/2020

Session Information
Service Code: 97151: BCBA Behavior Identification
Assessment / Reassessment Session Duration: 1:00
Session Date: 01/15/2026 Session Units: 4 units

Assessment Activities: 	Assessment Type Conducted: N/A
☐Review records: 	☐Functional Behavior Assessment
☐Direct observation/treatment of patient to inform treatment
goals.
☑Treatment plan development
☐Reviewing assessment results and treatment plans with
caregivers
☐Other:

Session Narrative:
Reviewed the treatment plan today.
Patient Name: Test Patient Patient DOB: 01/01/2020 Date of Service: 01/15/2026
Page 1 of 1 I - Page 1 of 1
"""


def test_SN_97151_11_passes_when_treatment_plan_development_checked():
    result = fields_module._check_SN_97151_11(_fields(SAMPLE_97151_ASSESSMENT_TEXT, "97151"))
    assert result["result"] == "pass"


def test_SN_97151_11_not_checkable_when_unchecked_and_no_keyword():
    unchecked_text = SAMPLE_97151_ASSESSMENT_TEXT.replace("☑Treatment plan development", "☐Treatment plan development")
    result = fields_module._check_SN_97151_11(_fields(unchecked_text, "97151"))
    assert result["result"] == "not_checkable"


def test_SN_97151_12_and_97153_19_share_the_same_keyword_logic():
    assert fields_module._check_SN_97151_12(_fields(SAMPLE_97153_TEXT, "97151"))["result"] == "pass"


def test_run_deterministic_checks_escalates_unimplemented_rules():
    rules = [
        {"rule_id": "SN-FAKE-01", "service_code": "97153", "check_type": "deterministic", "active": True},
        {"rule_id": "SN-97153-19", "service_code": "97153", "check_type": "deterministic", "active": True},
    ]
    det_results, escalated = fields_module.run_deterministic_checks(rules, _fields(SAMPLE_97153_TEXT))
    assert "SN-97153-19" in det_results
    assert len(escalated) == 1
    assert escalated[0]["rule_id"] == "SN-FAKE-01"


def test_data_point_bullets_handles_a_line_wrap_right_at_the_trailing_date():
    """BUG FOUND on a real document (batch_18page_3client_v2.pdf, Joseph
    Bergstein's 97153 note): a bullet's own '... for 5 min for\\n09/24/2026.'
    wraps its line break directly between 'for' and the date, which a
    literal single-space regex never matched -- silently dropping that
    bullet (and any other similarly-wrapped one) from the count entirely.
    """
    bullets = fields_module.data_point_bullets(_real_fields("single_doc_97153_bergstein_v2.pdf"))
    assert len(bullets) == 12  # exact real count, confirmed by hand against the source PDF
    assert len({g for _, g, _ in bullets}) == 12  # all 12 are genuinely distinct goals


def test_SN_97153_03_real_bergstein_document_no_longer_undercounts():
    # BUG: previously reported "1 distinct goal(s) across 3.00h" (a missing
    # re.DOTALL meant a goal description wrapping across a PDF line break
    # never matched at all) -- real count is 12, well over the 3/h bar.
    result = fields_module._check_SN_97153_03(_real_fields("single_doc_97153_bergstein_v2.pdf"))
    assert result["result"] == "pass"
    assert "12 distinct goal(s)" in result["evidence"]


def test_SN_97153_03_real_drummer_document_no_longer_undercounts():
    # BUG: previously reported "3 distinct goal(s) across 1.75h" -- real
    # count is 21 (13 bullets on one page + 8 on the next, all distinct).
    result = fields_module._check_SN_97153_03(_real_fields("single_doc_97153_drummer_v2.pdf"))
    assert result["result"] == "pass"
    assert "21 distinct goal(s)" in result["evidence"]


def test_SN_97153_15_real_bergstein_document_finds_the_session_summary():
    # BUG: the checker hardcoded 'Session Narrative:' as the section
    # header, but a real 97153 "Behavior Technician Direct Care Session
    # Note" uses 'Session Summary' instead (no colon) -- only 97151
    # "Assessment Session Note" templates use 'Session Narrative:'. This
    # silently returned "uncertain" for every real 97153 document, even
    # though Session Duration was extracted successfully in the same run.
    result = fields_module._check_SN_97153_15(_real_fields("single_doc_97153_bergstein_v2.pdf"))
    assert result["result"] in ("pass", "fail")  # a real verdict, not "uncertain"


def test_SN_97153_15_real_drummer_document_finds_the_session_summary():
    result = fields_module._check_SN_97153_15(_real_fields("single_doc_97153_drummer_v2.pdf"))
    assert result["result"] in ("pass", "fail")


def test_narrative_section_text_falls_back_across_known_headers():
    session_summary_text = "Session Summary\nSome real narrative content here.\nProvider Name: X"
    assert fields_module._narrative_section_text({"full_text": session_summary_text}).strip() == "Some real narrative content here."

    session_narrative_text = "Session Narrative:\nOther real narrative content.\nProvider Name: X"
    assert fields_module._narrative_section_text({"full_text": session_narrative_text}).strip() == "Other real narrative content."

    assert fields_module._narrative_section_text({"full_text": "neither header present"}) is None


def test_run_deterministic_checks_skips_inactive_and_non_deterministic_rules():
    rules = [
        {"rule_id": "SN-97153-19", "service_code": "97153", "check_type": "deterministic", "active": False},
        {"rule_id": "SN-97153-06", "service_code": "97153", "check_type": "judgment", "active": True},
    ]
    det_results, escalated = fields_module.run_deterministic_checks(rules, _fields(SAMPLE_97153_TEXT))
    assert det_results == {}
    assert escalated == []


def test_bcba_display_name_real_97151_cazi_document_falls_back_to_provider():
    """BUG found by direct inspection of the live app (Daniel Cazi's real
    97151 document, header card showed BCBA as a bare '-'): this note's
    own template has NO separate 'BCBA/LBA:' header field at all -- the
    provider IS the BCBA ('Provider Name: Cindy Rodriguez-Sumner, BCBA,
    LBA', one person, not two). bcba_lba_header_name() correctly returns
    None here (there's genuinely no second field); bcba_display_name()
    must fall back to the provider's own name instead of showing blank.
    """
    f = _real_fields("single_doc_97151_cazi_appendix1.pdf", service_code="97151")
    assert fields_module.bcba_lba_header_name(f) is None
    assert fields_module.bcba_display_name(f) == "Cindy Rodriguez-Sumner"


def test_bcba_display_name_real_97153_document_still_uses_the_real_header_field():
    """Scope check: a real 97153 note DOES carry a genuine, distinct
    'BCBA/LBA:' header field (the on-site provider there is a BT, not a
    BCBA, so the supervising BCBA is really a separate named person) --
    bcba_display_name() must keep using that real field, never fall back
    to the provider's own name on this service code.
    """
    f = _real_fields("single_doc_97153_bergstein_v2.pdf", service_code="97153")
    header_name = fields_module.bcba_lba_header_name(f)
    assert header_name is not None
    assert fields_module.bcba_display_name(f) == header_name


def test_bcba_display_name_does_not_guess_when_provider_has_no_bcba_credential():
    no_bcba_text = SAMPLE_97153_TEXT.replace("BCBA/LBA: John Doe BCBA, LBA\n", "")
    f = _fields(no_bcba_text)
    assert fields_module.bcba_lba_header_name(f) is None
    assert fields_module.bcba_display_name(f) is None


def test_SN_97153_05_unaffected_by_bcba_display_name_fallback():
    """Explicit scope confirmation (asked for directly in the bug report):
    SN-97153-05's own checker calls bcba_lba_header_name() directly and is
    untouched by bcba_display_name()'s fallback -- re-run the existing
    mismatch/match fixtures to confirm the rule's own real-data behavior
    is identical to before this fix.
    """
    supervised_text = SAMPLE_97153_TEXT.replace(
        "Was this session Supervised by the BCBA/LBA?\nNo", "Was this session Supervised by the BCBA/LBA?\nYes",
    )
    assert fields_module._check_SN_97153_05(_fields(supervised_text))["result"] == "fail"
    matching_text = supervised_text.replace("BCBA/LBA: John Doe BCBA, LBA", "BCBA/LBA: Jane Smith BCBA, LBA")
    assert fields_module._check_SN_97153_05(_fields(matching_text))["result"] == "pass"
