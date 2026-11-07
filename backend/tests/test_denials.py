"""Denials and appeals: the 835 format, the mock payer, triage against the answer keys,
appeal grounding, and the whole flow through the API."""

import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.agents.appeals import draft_appeal
from app.agents.denials import choose_policy, denial_groups, triage_denial
from app.documents.pdf import parse_pdf
from app.revenue import payer_claim
from app.verticals.healthcare import payer
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.samples import FILE_KINDS, list_cases, truth_to_extracted
from app.verticals.healthcare.x12_835 import X12Error, build_835, parse_835
from tests.conftest import login
from tests.fake_llm import FakeLLM

DENIAL_CASES = list_cases("den_")
TODAY = date.today()


def _packet(case):
    docs = {FILE_KINDS[f]: (i, parse_pdf(f"{case['path']}/{f}")) for i, f in enumerate(case["files"])}
    record = truth_to_extracted(case["record"], docs)
    claim = SimpleNamespace(number=f"CLM-T{case['case_id'][-3:]}", extracted=record,
                            payer_name=case["record"]["claim_form"]["payer_name"],
                            service_date=record["claim_form"]["service_date"]["value"])
    return claim, record, docs


def _adjudicate(case, frequency="1", modifiers=None):
    claim, record, docs = _packet(case)
    request = payer_claim(claim, frequency, modifiers)
    return claim, record, docs, payer.adjudicate(request, TODAY)


# --- 835 --------------------------------------------------------------------------------------

def test_835_round_trip_keeps_every_amount():
    case = next(c for c in DENIAL_CASES if c["scenario"] == "bundling_mod25")
    _, _, _, clp = _adjudicate(case)
    remit = payer.remittance(case["record"]["claim_form"]["payer_name"], {"name": "Clinic", "npi": "1234567893"},
                             [clp], TODAY, "TRACE1")
    raw = build_835(remit, "NWH01", "HEALTHOPS", 42, datetime.now(timezone.utc))
    parsed = parse_835(raw)
    assert parsed["warnings"] == []  # every claim balances: charge = paid + adjustments
    got = parsed["claims"][0]
    assert (got["claim_number"], got["status_code"], got["paid"]) == (clp["claim_number"], clp["status_code"], clp["paid"])
    assert [(l["cpt"], l["paid"], [(a["group"], a["code"], a["amount"]) for a in l["adjustments"]], l["remarks"])
            for l in got["lines"]] == \
           [(l["cpt"], l["paid"], [(a["group"], a["code"], a["amount"]) for a in l["adjustments"]], l["remarks"])
            for l in clp["lines"]]


def test_835_separators_come_from_the_isa_segment():
    # A hand-written 835 using | as the element separator, > for components and no newlines.
    raw = ("ISA|00|          |00|          |ZZ|PAYER          |ZZ|PROVIDER       |261101|1200|^|00501|000000007|0|P|>~"
           "GS|HP|PAYER|PROVIDER|20261101|1200|7|X|005010X221A1~ST|835|0001~BPR|I|80|C|ACH~TRN|1|T99|1~"
           "N1|PR|Test Payer|XV|TP1~N1|PE|Clinic|XX|1234567893~LX|1~CLP|CLM-1|1|200|80|20|12|PCN1|11|1~"
           "NM1|QC|1|DOE|JANE||||MI|M123~SVC|HC>99213>25|200|80||1~DTM|472|20261020~"
           "CAS|CO|45|100~CAS|PR|1|20~AMT|B6|100~SE|14|0001~GE|1|7~IEA|1|000000007~")
    parsed = parse_835(raw)
    line = parsed["claims"][0]["lines"][0]
    assert line["cpt"] == "99213" and line["modifiers"] == ["25"] and line["service_date"] == "2026-10-20"
    assert [a["code"] for a in line["adjustments"]] == ["45", "1"] and parsed["warnings"] == []


def test_835_rejects_a_wrong_segment_count():
    case = DENIAL_CASES[0]
    _, _, _, clp = _adjudicate(case)
    raw = build_835(payer.remittance(case["record"]["claim_form"]["payer_name"], {"name": "C", "npi": "1"}, [clp],
                                     TODAY, "T"), "P", "R", 1, datetime.now(timezone.utc))
    with pytest.raises(X12Error, match="SE says"):
        parse_835(raw.replace("SE*", "SE*9", 1))
    with pytest.raises(X12Error):
        parse_835("not an edi file")


# --- payer ------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", DENIAL_CASES, ids=[c["case_id"] for c in DENIAL_CASES])
def test_payer_decides_each_scenario_as_planted(case):
    _, _, _, clp = _adjudicate(case)
    key = case["denial"]
    assert clp["status_code"] == key["remit_status"]
    assert sorted(f"{g['group']}-{g['code']}" for g in denial_groups(clp)) == sorted(key["codes"])


def test_corrected_claim_with_modifier_25_is_paid():
    case = next(c for c in DENIAL_CASES if c["scenario"] == "bundling_mod25")
    _, _, _, clp = _adjudicate(case, "7", {"99214": ["25"]})
    assert denial_groups(clp) == [] and all(l["paid"] > 0 for l in clp["lines"])


# --- triage -----------------------------------------------------------------------------------

def _triage(case, llm):
    claim, record, docs, clp = _adjudicate(case)
    results = []
    for group in denial_groups(clp):
        context = {"payer_name": claim.payer_name, "service_date": claim.service_date, "remit_date": TODAY.isoformat(),
                   "first_submitted_on": TODAY.isoformat(), "claim_cpts": [l["cpt"] for l in clp["lines"]]}
        results.append((group, triage_denial(group, record, docs, context, llm)))
    return results


@pytest.mark.parametrize("case", [c for c in DENIAL_CASES if c["denial"]["codes"]],
                         ids=[c["case_id"] for c in DENIAL_CASES if c["denial"]["codes"]])
def test_triage_matches_the_answer_key(case):
    key = case["denial"]
    [(group, result)] = _triage(case, FakeLLM())
    assert (result.category, result.action, result.policy_id) == (key["category"], key["action"], key["policy_id"])
    for criterion in result.criteria:
        if criterion["id"] in key.get("criteria", {}):
            assert criterion["met"] == key["criteria"][criterion["id"]]
            if criterion["met"]:
                assert criterion["evidence"]["bbox"]  # pinned to the note
    if result.action in ("appeal", "corrected_claim", "bill_patient"):
        assert result.deadline and result.expected_recovery > 0
    else:
        assert result.expected_recovery == 0


def test_policy_search_finds_the_governing_policy():
    for case in DENIAL_CASES:
        key = case["denial"]
        if not key["codes"]:
            continue
        _, record, _, clp = _adjudicate(case)
        policy, retrieved = choose_policy(denial_groups(clp)[0], record)
        assert policy["id"] == key["policy_id"], (case["case_id"], retrieved)


def test_a_criterion_without_a_real_quote_is_not_met():
    """The model claims every criterion is met but quotes text that isn't in the note."""
    case = next(c for c in DENIAL_CASES if c["scenario"] == "med_necessity_unsupported")

    class Overconfident(FakeLLM):
        def _assess(self, payload):
            return {"criteria": [{"id": c["id"], "met": True, "quote": "8 weeks of physical therapy failed"}
                                 for c in payload["policy"]["criteria"]], "summary": "all met"}

    [(_, result)] = _triage(case, Overconfident())
    assert not any(c["met"] for c in result.criteria) and result.action == "write_off"
    assert all(c["model_said_met"] for c in result.criteria)


# --- appeals ----------------------------------------------------------------------------------

def _facts(case, clp, group):
    form = case["record"]["claim_form"]
    return {"claim_number": "CLM-T1", "payer_claim_number": clp["payer_control_number"], "payer": form["payer_name"],
            "patient": form["patient_name"], "member_id": form["member_id"], "provider": form["provider_name"],
            "service_date": clp["service_date"], "remit_date": TODAY.isoformat(),
            "denial_code": f"{group['group']}-{group['code']}", "denial_reason": "reason",
            "remark_codes": group["remarks"], "denied_amount": group["amount"], "billed_amount": clp["charge"],
            "services": [{"cpt": l["cpt"], "charge": l["charge"]} for l in group["lines"]],
            "diagnosis_codes": form["diagnosis_codes"], "prior_auth_number": form.get("prior_auth_number")}


def test_appeal_keeps_only_grounded_paragraphs_and_the_payer_overturns():
    case = next(c for c in DENIAL_CASES if c["scenario"] == "med_necessity_supported")
    llm = FakeLLM(appeal_extra=[
        {"kind": "argument", "text": "An earlier visit on 03/14/2019 showed the same.",
         "citations": [{"source": "clinical_note", "quote": "Symptoms persisted despite 8 weeks of physical therapy"}]},
        {"kind": "argument", "text": "Guidelines support it.", "citations": [{"source": "policy", "quote": "always covered"}]},
    ])
    claim, record, docs, clp = _adjudicate(case)
    [(group, triage)] = _triage(case, llm)
    draft = draft_appeal(llm, _facts(case, clp, group), triage.as_json(), docs, get_policy(triage.policy_id))
    assert draft.stats["removed_paragraphs"] == 2  # the invented date, and the made-up policy quote
    assert any("03/14/2019" in r["text"] and r["reason"].startswith("Mentions") for r in draft.removed)
    assert all(c["verified"] and c["evidence"] for p in draft.paragraphs for c in p["citations"])
    assert draft.paragraphs[-1]["kind"] == "request"

    citations = [c for p in draft.paragraphs for c in p["citations"]]
    decision = payer.review_appeal("medical_necessity", ["64483"], citations, record, claim.service_date,
                                   get_policy("MP-2041"))
    assert decision.overturned and decision.missing == []
    # The same appeal without the imaging sentence doesn't meet C3, so the payer upholds.
    partial = [c for c in citations if "MRI" not in c["quote"]]
    decision = payer.review_appeal("medical_necessity", ["64483"], partial, record, claim.service_date,
                                   get_policy("MP-2041"))
    assert not decision.overturned and decision.missing == ["C3"]


def test_unverified_citations_never_count_for_the_payer():
    case = next(c for c in DENIAL_CASES if c["scenario"] == "med_necessity_supported")
    claim, record, _, _ = _adjudicate(case)
    fake = [{"source": "clinical_note", "quote": q, "verified": False}
            for q in case["denial"]["criteria_quotes"].values()]
    assert not payer.review_appeal("medical_necessity", ["64483"], fake, record, claim.service_date,
                                   get_policy("MP-2041")).overturned


# --- through the API --------------------------------------------------------------------------

def _wait(fn, check, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if check(value):
            return value
        time.sleep(0.2)
    raise AssertionError(f"timed out; last value: {str(value)[:600]}")


def _submit(client, scenario):
    case = next(c for c in DENIAL_CASES if c["scenario"] == scenario)
    login(client, "biller")
    claim = client.post(f"/api/samples/{case['case_id']}/load").json()
    get = lambda: client.get(f"/api/claims/{claim['id']}").json()  # noqa: E731
    _wait(get, lambda c: c["status"] in ("ready", "needs_review"))
    assert client.post(f"/api/claims/{claim['id']}/decision", json={"action": "approve"}).status_code == 200
    return claim["id"], get


def _denial_of(client, claim_id):
    claim = _wait(lambda: client.get(f"/api/claims/{claim_id}").json(),
                  lambda c: c["denials"] and all(d["status"] != "triage" for d in c["denials"]))
    return client.get(f"/api/denials/{claim['denials'][0]['id']}").json()


def test_authorization_denial_is_appealed_and_paid(client):
    claim_id, get = _submit(client, "auth_not_on_file")
    denial = _denial_of(client, claim_id)
    assert (denial["code"], denial["recommended_action"], denial["claim"]["status"]) == ("CO-197", "appeal", "denied")
    assert "ISA*" in client.get(f"/api/remittances/{denial['remittance']['id']}/raw").text

    denial = _wait(lambda: client.get(f"/api/denials/{denial['id']}").json(), lambda d: d["appeal"]["status"] == "draft")
    draft = denial["appeal"]["draft"]
    assert draft["stats"]["removed_paragraphs"] == 2 and draft["paragraphs"]
    assert any(c["source"] == "prior_authorization" for p in draft["paragraphs"] for c in p["citations"])

    # The biller rewrites one paragraph and adds one of their own; citations stay the server's.
    edits = [{"source_index": i, "text": p["text"]} for i, p in enumerate(draft["paragraphs"])]
    edits[0]["text"] += " Please see the enclosed determination letter."
    edits.insert(-1, {"source_index": None, "text": "Our office called on the date of service to confirm."})
    login(client, "intake")
    assert client.post(f"/api/appeals/{denial['appeal']['id']}/send", json={"paragraphs": edits}).status_code == 403
    login(client, "biller")
    r = client.post(f"/api/appeals/{denial['appeal']['id']}/send", json={"paragraphs": edits})
    assert r.status_code == 200 and r.json()["status"] == "appeal_sent"

    denial = _wait(lambda: client.get(f"/api/denials/{denial['id']}").json(), lambda d: d["status"] == "overturned")
    assert denial["recovered"] > 0 and denial["appeal"]["status"] == "overturned"
    claim = get()
    assert claim["status"] == "paid" and [r["kind"] for r in claim["remittances"]] == ["original", "appeal"]
    assert claim["financials"]["paid"] == denial["recovered"]
    assert client.get("/api/audit/verify").json()["ok"]


def test_bundling_denial_is_fixed_with_a_corrected_claim(client):
    claim_id, get = _submit(client, "bundling_mod25")
    denial = _denial_of(client, claim_id)
    assert (denial["code"], denial["recommended_action"]) == ("CO-97", "corrected_claim")
    assert denial["triage"]["fix"] == {"cpt": "99214", "add_modifier": "25"}
    r = client.post(f"/api/denials/{denial['id']}/action", json={"action": "corrected_claim"})
    assert r.status_code == 200, r.text
    denial = _wait(lambda: client.get(f"/api/denials/{denial['id']}").json(),
                   lambda d: d["status"] == "paid_on_resubmission")
    claim = get()
    kinds = [(r["kind"], r["claim"]["status_code"]) for r in claim["remittances"]]
    assert kinds == [("original", "1"), ("corrected", "22"), ("corrected", "1")]  # reversal, then the replacement
    assert claim["status"] == "paid" and [s["frequency"] for s in claim["submissions"]] == ["1", "7"]
    paid_lines = claim["remittances"][-1]["claim"]["lines"]
    assert {l["cpt"]: l["modifiers"] for l in paid_lines}["99214"] == ["25"]
    # Net payment: the original 20552 payment was reversed and paid again with 99214.
    assert claim["financials"]["paid"] == round(sum(l["paid"] for l in paid_lines), 2)


def test_patient_balance_and_write_off(client):
    claim_id, get = _submit(client, "deductible")
    denial = _denial_of(client, claim_id)
    assert denial["code"] == "PR-1" and get()["status"] == "paid"  # a deductible isn't a denial
    r = client.post(f"/api/denials/{denial['id']}/action", json={"action": "bill_patient"})
    assert r.json()["status"] == "patient_billed"

    claim_id, get = _submit(client, "timely_filing")
    denial = _denial_of(client, claim_id)
    assert (denial["code"], denial["recommended_action"]) == ("CO-29", "write_off")
    assert client.post(f"/api/denials/{denial['id']}/action", json={"action": "bill_patient"}).status_code == 400
    assert client.post(f"/api/denials/{denial['id']}/action", json={"action": "write_off"}).status_code == 400
    r = client.post(f"/api/denials/{denial['id']}/action", json={"action": "write_off", "note": "Filed late"})
    assert r.json()["status"] == "written_off" and get()["status"] == "closed"


def test_unsupported_appeal_is_upheld(client):
    claim_id, _ = _submit(client, "med_necessity_unsupported")
    denial = _denial_of(client, claim_id)
    assert denial["recommended_action"] == "write_off" and denial["appeal"] is None
    # The biller appeals anyway; the payer upholds because no criterion is shown.
    client.post(f"/api/denials/{denial['id']}/action", json={"action": "draft_appeal"})
    denial = _wait(lambda: client.get(f"/api/denials/{denial['id']}").json(),
                   lambda d: d["appeal"] and d["appeal"]["status"] == "draft")
    edits = [{"source_index": i, "text": p["text"]} for i, p in enumerate(denial["appeal"]["draft"]["paragraphs"])]
    client.post(f"/api/appeals/{denial['appeal']['id']}/send", json={"paragraphs": edits})
    denial = _wait(lambda: client.get(f"/api/denials/{denial['id']}").json(), lambda d: d["status"] == "upheld")
    assert "C1" in denial["appeal"]["outcome_reason"] and denial["claim"]["status"] == "denied"


def test_835_import_matches_claims_by_number(client):
    claim_id, get = _submit(client, "paid_clean")
    claim = _wait(get, lambda c: c["status"] == "paid")
    raw = client.get(f"/api/remittances/{claim['remittances'][0]['id']}/raw").text
    r = client.post("/api/remittances/import", files={"file": ("era.835", raw.replace(claim["number"], "CLM-NOPE"))})
    assert r.json() == {"matched": 0, "unknown_claims": ["CLM-NOPE"], "remittances": []}
    assert client.post("/api/remittances/import", files={"file": ("x.835", "hello")}).status_code == 400
