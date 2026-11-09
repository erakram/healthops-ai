"""837P: what the provider writes is exactly what the payer reads."""

from datetime import datetime, timezone

import pytest

from app.revenue import payer_claim
from app.verticals.healthcare.x12_835 import X12Error
from app.verticals.healthcare.x12_837 import build_837, parse_837
from tests.test_denials import DENIAL_CASES, _packet

KEYS = ("claim_number", "frequency", "payer_name", "member_id", "patient_last", "patient_first", "service_date",
        "prior_auth_number", "provider_name", "provider_npi", "diagnosis_codes", "patient_dob")


@pytest.mark.parametrize("scenario", ["auth_not_on_file", "bundling_mod25", "deductible"])
def test_837_round_trip(scenario):
    case = next(c for c in DENIAL_CASES if c["scenario"] == scenario)
    claim, _, _ = _packet(case)
    request = payer_claim(claim, "7" if scenario == "bundling_mod25" else "1",
                          {"99214": ["25"]} if scenario == "bundling_mod25" else None)
    request["original_reference"] = "PYR-ORIG" if request["frequency"] == "7" else None
    text = build_837(request, "NWH01", 7, datetime.now(timezone.utc))
    assert text.startswith("ISA") and "ST*837*" in text and "CLM*" in text
    back = parse_837(text)
    assert {k: back.get(k) for k in KEYS} == {k: request.get(k) for k in KEYS}
    assert [(l["cpt"], l["modifiers"], l["units"], l["charge"]) for l in back["lines"]] == \
           [(l["cpt"], l["modifiers"], l["units"], l["charge"]) for l in request["lines"]]
    assert back["original_reference"] == request["original_reference"]


def test_837_is_checked():
    case = DENIAL_CASES[0]
    claim, _, _ = _packet(case)
    text = build_837(payer_claim(claim), "NWH01", 8, datetime.now(timezone.utc))
    with pytest.raises(X12Error, match="SE says"):
        parse_837(text.replace("SE*", "SE*9", 1))
    with pytest.raises(X12Error, match="total"):
        parse_837(text.replace("CLM*" + claim.number + "*", "CLM*" + claim.number + "*1", 1))
