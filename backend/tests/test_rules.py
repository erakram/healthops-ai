"""The rules engine against every sample case's answer key: each planted problem must be
found, and nothing else may be flagged."""

import pytest

from app.documents.pdf import parse_pdf
from app.verticals.healthcare.rules import run_rules
from app.verticals.healthcare.samples import FILE_KINDS, list_cases, truth_to_extracted
from app.verticals.healthcare.schema import normalize_name, parse_date, parse_money, valid_icd10, valid_npi

CASES = [c for c in list_cases("") if "pa" not in c]  # claim packets; pa_* are orders


def _record(case):
    parsed = {FILE_KINDS[f]: (i, parse_pdf(f"{case['path']}/{f}")) for i, f in enumerate(case["files"])}
    return truth_to_extracted(case["record"], parsed)


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_rules_find_exactly_the_planted_problems(case):
    codes = {f.code for f in run_rules(_record(case))}
    # Prompt injection is detected during extraction, not by the rules.
    expected = set(case["expected_findings"]) - {"PROMPT-INJECTION"}
    assert codes == expected, f"{case['scenario']}: expected {expected}, got {codes}"


def test_evidence_boxes_are_found_for_answer_key_values():
    record = _record(CASES[0])
    form = record["claim_form"]
    assert form["member_id"]["evidence"] and form["total_charge"]["evidence"]
    x0, y0, x1, y1 = form["member_id"]["evidence"]["bbox"]
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def test_duplicate_claim_is_flagged():
    record = _record(CASES[0])
    form = record["claim_form"]
    other = {"number": "CLM-1", "member_id": form["member_id"]["value"], "service_date": form["service_date"]["value"],
             "cpt_codes": [i["cpt"] for i in form["line_items"]["value"]]}
    assert "DUPLICATE-CLAIM" in {f.code for f in run_rules(record, [other])}


def test_normalisation_helpers():
    assert valid_npi("1234567893")  # the standard CMS example NPI
    assert not valid_npi("1234567890")
    assert valid_icd10("S83.241A") and not valid_icd10("83.24")
    assert normalize_name("WHITAKER, JAMES") == normalize_name("James Whitaker")
    assert parse_date("March 4, 2025") == parse_date("03/04/2025")
    assert parse_money("$1,240.50") == 1240.5


def test_extractor_unwraps_codes_returned_as_json_strings():
    # Regression from evals/reports: gpt-oss once returned a diagnosis code as a JSON string.
    from app.agents.extraction import extract_document
    from app.core.llm import LLMResult

    case = next(c for c in CASES if c["record"]["claim_form"]["diagnosis_codes"])
    pdf = parse_pdf(f"{case['path']}/claim_form.pdf")
    code = case["record"]["claim_form"]["diagnosis_codes"][0]

    class OneShot:
        def complete_json(self, system, user, model=None):
            return LLMResult({"document_type": "claim_form", "fields": {},
                              "diagnosis_codes": [f'{{"code":"{code}","quote":"{code}"}}']}, "fake", 1, 1, 1)

    result = extract_document(OneShot(), pdf, 1)
    assert [c["code"] for c in result.fields["diagnosis_codes"]["value"]] == [code]


def test_reviewer_ignores_a_heading_as_evidence():
    """Seen live: the model flagged a correct diagnosis code and quoted only the section heading."""
    from app.agents.reviewer import review_claim
    from app.core.llm import LLMResult

    case = next(c for c in CASES if c["scenario"] == "note_mismatch")
    note = parse_pdf(f"{case['path']}/clinical_note.pdf")

    class Heading:
        def complete_json(self, system, user, model=None):
            return LLMResult({"issues": [{"title": "Code mismatch", "severity": "medium", "detail": "x",
                                          "quote": "Assessment and plan"}]}, "fake", 1, 1, 1)

    result = review_claim(_record(case), [], Heading(), (1, note))
    assert result.dropped_ai_findings == 1 and not [f for f in result.findings if f.source == "ai"]
