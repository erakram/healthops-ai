"""Accounts receivable: 276/277, the follow-up rules, and the four silent-claim scenarios
through the API, each ending with the payer's 835."""

import time
from datetime import date, datetime, timezone

import pytest

from app.ar import draft_note, recommend
from app.core.llm import LLMResult
from app.verticals.healthcare import payer
from app.verticals.healthcare.samples import list_cases
from app.verticals.healthcare.x12_276_277 import X12Error, build_276, build_277, parse_277
from tests.conftest import login

INQUIRY = {"claim_number": "CLM-1", "payer_name": "Cedar Ridge Health", "payer_id": "CRH03", "provider_name": "Clinic",
           "provider_npi": "1234567893", "patient_last": "DOE", "patient_first": "JANE", "member_id": "CRH1",
           "charge": 145.0, "service_date": "2026-10-01", "trace": "T-1"}


@pytest.mark.parametrize("status", [
    {"category": "D0", "code": "35", "effective": "2026-11-01", "message": "Not found"},
    {"category": "F1", "code": "65", "effective": "2026-10-20", "paid_amount": 95.7, "paid_on": "2026-10-20",
     "check_number": "EFT00012345", "payer_claim_number": "CRH9", "message": "Claim paid"},
])
def test_276_277_round_trip(status):
    now = datetime.now(timezone.utc)
    assert "ST*276" in build_276(INQUIRY, "HEALTHOPS", "CRH03", 1, now)
    parsed = parse_277(build_277(INQUIRY, status, "CRH03", "HEALTHOPS", 2, now))
    got = parsed["statuses"][0]
    assert (parsed["claim_number"], got["category"], got["code"]) == ("CLM-1", status["category"], status["code"])
    assert got["paid_amount"] == status.get("paid_amount") and got["check_number"] == status.get("check_number")
    assert got["message"] == status["message"]
    with pytest.raises(X12Error):
        parse_277(build_277(INQUIRY, status, "P", "R", 3, now).replace("SE*", "SE*9", 1))


def test_follow_up_rules():
    row = {"claim_number": "CLM-1", "submitted_on": "2026-08-01", "filing_deadline": "2027-01-01",
           "days_to_filing_deadline": 50}
    cases = {"D0": "resubmit", "P3": "send_records", "F1": "request_remittance", "P1": "wait", "A7": "correct"}
    for category, action in cases.items():
        status = {"category": category, "code": "x", "code_text": "x", "category_text": "x", "message": None,
                  "paid_amount": 10.0 if category == "F1" else None, "check_number": None}
        assert recommend(status, row)[0] == action


def test_a_draft_with_an_invented_number_falls_back_to_the_template():
    class Inventive:
        def complete_json(self, system, user, model=None):
            return LLMResult({"channel": "portal message", "subject": "Follow-up",
                              "message": "Claim CLM-1 was paid by check 99887766 on 2026-01-05."}, "fake", 1, 1, 1)

    facts = {"claim_number": "CLM-1", "patient": "Jane Doe", "payer": "Cedar Ridge Health", "service_date": "2026-10-01",
             "first_submitted_on": "2026-10-03", "original_reference": "PYR-1", "billed_amount": 145.0}
    note, _ = draft_note(Inventive(), "request_remittance", facts)
    assert note["drafted_by"] == "template" and note["rejected_because"]


def _wait(client, claim_id, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        claim = client.get(f"/api/claims/{claim_id}").json()
        if claim["status"] in statuses:
            return claim
        time.sleep(0.2)
    raise AssertionError(f"claim stuck in {claim['status']}")


def _seeded(client, scenario):
    """The seeded silent claim for a scenario (seeded from the har_* cases)."""
    members = {c["record"]["claim_form"]["member_id"] for c in list_cases("har_") if c["ar"]["scenario"] == scenario}
    for row in client.get("/api/ar").json():
        claim = client.get(f"/api/claims/{row['claim_id']}").json()
        if claim["extracted"]["claim_form"]["member_id"]["value"] in members:
            return row
    raise AssertionError(f"no seeded {scenario} claim")


@pytest.mark.parametrize("scenario,category,action", [
    ("lost", "D0", "resubmit"), ("records_pending", "P3", "send_records"),
    ("paid_no_835", "F1", "request_remittance"),
])
def test_silent_claim_is_followed_up_until_paid(client, scenario, category, action):
    login(client, "biller")
    row = _seeded(client, scenario)
    assert row["due_for_follow_up"] and row["bucket"] in ("0-30", "31-60", "61-90", "90+")
    detail = client.post(f"/api/ar/{row['claim_id']}/check").json()
    check = detail["checks"][-1]
    assert check["status"]["category"] == category and check["action"] == action
    assert "ST*276" in check["x12_276"] and "STC*" in check["x12_277"] and check["note"]["message"]
    r = client.post(f"/api/ar/{row['claim_id']}/action", json={"action": action})
    assert r.status_code == 200
    claim = _wait(client, row["claim_id"], {"paid"})
    assert claim["financials"]["paid"] > 0
    assert row["claim_id"] not in {r["claim_id"] for r in client.get("/api/ar").json()}
    if scenario == "lost":
        assert len(claim["submissions"]) == 2


def test_in_process_claim_means_wait(client):
    login(client, "biller")
    row = _seeded(client, "in_process")
    detail = client.post(f"/api/ar/{row['claim_id']}/check").json()
    assert detail["checks"][-1]["action"] == "wait" and detail["next_check_on"] > date.today().isoformat()
    assert client.post(f"/api/ar/{row['claim_id']}/action", json={"action": "wait"}).status_code == 200
    assert client.get(f"/api/claims/{row['claim_id']}").json()["status"] == "submitted"
    summary = client.get("/api/ar/summary").json()
    assert summary["claims"] >= 1 and sum(b["count"] for b in summary["buckets"]) == summary["claims"]


def test_transaction_names_are_not_mistaken_for_invented_facts():
    class Plain:
        def complete_json(self, system, user, model=None):
            return LLMResult({"channel": "portal message", "subject": "835 copy",
                              "message": "Claim CLM-1 shows as paid in your 277 response, but we never received the 835. "
                                         "Please resend the remittance."}, "fake", 1, 1, 1)

    facts = {"claim_number": "CLM-1", "patient": "Jane Doe", "payer": "Cedar Ridge Health", "service_date": "2026-10-01",
             "first_submitted_on": "2026-10-03", "original_reference": "PYR-1"}
    note, _ = draft_note(Plain(), "request_remittance", facts)
    assert note["drafted_by"] == "agent"
