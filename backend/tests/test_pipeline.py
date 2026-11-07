"""End to end through the API: sample packet -> agents -> findings -> human decision -> payer."""

import time

from app.verticals.healthcare.samples import list_cases
from tests.conftest import login


def _case(scenario: str) -> str:
    return next(c["case_id"] for c in list_cases("case_") if c["scenario"] == scenario)


def _wait(client, claim_id, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        claim = client.get(f"/api/claims/{claim_id}").json()
        if claim["status"] in statuses:
            return claim
        time.sleep(0.2)
    raise AssertionError(f"claim stuck in {claim['status']}: {[e['message'] for e in claim['events']]}")


def test_requires_sign_in(client):
    client.cookies.clear()
    assert client.get("/api/claims").status_code == 401


def test_problem_claim_goes_to_review_then_approval_submits_once(client):
    login(client, "intake")
    claim = client.post(f"/api/samples/{_case('amount_mismatch')}/load").json()
    claim = _wait(client, claim["id"], {"needs_review"})
    codes = {f["code"] for f in claim["findings"]}
    assert "AMOUNT-MISMATCH" in codes
    assert claim["extracted"]["claim_form"]["total_charge"]["evidence"]  # every value is pinned to the page

    # Intake can't approve; billers can.
    assert client.post(f"/api/claims/{claim['id']}/decision", json={"action": "approve"}).status_code == 403
    login(client, "biller")
    assert client.post(f"/api/claims/{claim['id']}/decision", json={"action": "approve"}).status_code == 200
    claim = _wait(client, claim["id"], {"submitted"})
    assert claim["submission"]["payer_reference"].startswith("PYR-")
    assert client.get("/api/audit/verify").json()["ok"]


def test_reviewer_correction_revalidates_the_claim(client):
    login(client, "biller")
    claim = client.post(f"/api/samples/{_case('amount_mismatch')}/load").json()
    claim = _wait(client, claim["id"], {"needs_review"})
    claim_total = claim["extracted"]["claim_form"]["total_charge"]["raw"]
    r = client.patch(f"/api/claims/{claim['id']}/fields",
                     json={"document_type": "invoice", "field": "total_amount", "value": claim_total})
    assert r.status_code == 200
    claim = _wait(client, claim["id"], {"needs_review", "ready"})
    assert "AMOUNT-MISMATCH" not in {f["code"] for f in claim["findings"]}


def test_prompt_injection_is_flagged_not_obeyed(client):
    login(client, "intake")
    claim = client.post(f"/api/samples/{_case('prompt_injection')}/load").json()
    claim = _wait(client, claim["id"], {"needs_review"})
    injection = [f for f in claim["findings"] if f["code"] == "PROMPT-INJECTION"]
    assert injection and injection[0]["severity"] == "critical" and claim["risk"] == "high"


def test_ai_reviewer_catches_what_rules_cannot(client):
    login(client, "intake")
    claim = client.post(f"/api/samples/{_case('note_mismatch')}/load").json()
    claim = _wait(client, claim["id"], {"needs_review"})
    ai = [f for f in claim["findings"] if f["source"] == "ai"]
    assert ai and ai[0]["evidence"][0]["text"].startswith("Chest X-ray was deferred")


def test_reject_needs_a_reason(client):
    login(client, "biller")
    claim = client.post(f"/api/samples/{_case('clean')}/load").json()
    claim = _wait(client, claim["id"], {"ready", "needs_review"})
    assert client.post(f"/api/claims/{claim['id']}/decision", json={"action": "reject"}).status_code == 400
    r = client.post(f"/api/claims/{claim['id']}/decision", json={"action": "reject", "note": "Duplicate of a paper claim"})
    assert r.json()["status"] == "rejected"


def test_dashboard_has_history_and_live_runs(client):
    login(client, "biller")
    data = client.get("/api/dashboard").json()
    assert data["kpis"]["total_claims"] > 20
    assert data["kpis"]["ai_calls"] > 0
