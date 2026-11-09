"""Patient identifiers never reach the model, and the model's answers still work."""

import json

import pytest

from app.core.llm import LLMResult
from app.core.phi import Masker, MaskingLLM
from app.documents.pdf import parse_pdf
from app.verticals.healthcare.samples import list_cases

CASES = [c for c in list_cases("") if c["case_id"].startswith(("case_", "pa_", "enc_"))][::3]


def _identifiers(case: dict) -> list[str]:
    values = []
    for doc in case["record"].values():
        for key in ("patient_name", "member_name", "member_id", "patient_dob"):
            if doc.get(key):
                values.append(str(doc[key]))
    return values


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_no_identifier_survives_masking(case):
    masker = Masker()
    for name in case["files"]:
        masked = masker.mask(parse_pdf(f"{case['path']}/{name}").text)
        for value in _identifiers(case):
            assert value not in masked, f"{value} leaked from {name}"
            if "," not in value and " " in value:
                first, *_, last = value.split()
                assert f"{last.upper()}, {first.upper()}" not in masked


def test_the_answer_comes_back_unmasked():
    case = next(c for c in list_cases("case_") if c["scenario"] == "clean")
    text = parse_pdf(f"{case['path']}/claim_form.pdf").text
    seen = []

    class Echo:
        def complete_json(self, system, user, model=None):
            seen.append(user)
            token = next(t for t in ("[PERSON_1]", "[MEMBER_ID_1]") if t in user)
            return LLMResult({"fields": {"value": token, "quote": f"Member ID: {token}", "list": [token]}}, "echo", 1, 1, 1)

    result = MaskingLLM(Echo()).complete_json("system", text)
    record = case["record"]["claim_form"]
    assert record["patient_name"] not in seen[0] and record["member_id"] not in seen[0]
    restored = json.dumps(result.data)
    assert "[PERSON_" not in restored and "[MEMBER_ID_" not in restored
    assert record["patient_name"] in restored or record["patient_name"].upper().replace(" ", ", ") in restored \
        or record["member_id"] in restored
