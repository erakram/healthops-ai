"""Prior authorization: FHIR PAS round trip, the payer's rules, criteria against the answer
keys, and the whole flow through the API (including the addendum loop)."""

import time
from datetime import date, datetime, timezone

import pytest

from app.agents.authorization import assess_documentation, check_requirement, draft_request
from app.documents.pdf import parse_pdf
from app.verticals.healthcare import fhir_pas, payer
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.samples import FILE_KINDS, list_cases, truth_to_extracted
from tests.conftest import login
from tests.fake_llm import FakeLLM

PA_CASES = list_cases("pa_")
TODAY = date.today()


def _case(scenario: str) -> dict:
    return next(c for c in PA_CASES if c["scenario"] == scenario)


def _packet(case: dict, with_addendum: bool = False):
    docs = {FILE_KINDS[f]: (i, parse_pdf(f"{case['path']}/{f}")) for i, f in enumerate(case["files"])}
    record = truth_to_extracted(case["record"], docs)
    notes = {"clinical_note": docs["clinical_note"]}
    if with_addendum:
        notes["clinical_note_2"] = (99, parse_pdf(f"{case['path']}/extra/addendum_note.pdf"))
    return record, docs, notes


# --- FHIR and payer ------------------------------------------------------------------------

def _order(case: dict) -> dict:
    form = case["record"]["service_order"]
    return {"request_number": "PAR-1", "attempt": 1, "order_number": form["order_number"], "urgency": form["urgency"],
            "scheduled_date": "2026-12-01", "patient": {"name": form["patient_name"], "dob": "1970-01-01",
                                                        "member_id": form["member_id"]},
            "payer": {"name": form["payer_name"], "id": "X"}, "provider": {"name": form["ordering_provider"], "npi": "1"},
            "services": [{k: li[k] for k in ("cpt", "description", "units")} for li in form["line_items"]],
            "diagnosis_codes": form["diagnosis_codes"]}


def _request(case: dict, quotes: list[str], attempt: int = 1, with_addendum: bool = False):
    _, docs, notes = _packet(case, with_addendum)
    attached = [{"title": "Clinical note" if k == "clinical_note" else "Clinical note (addendum)", "text": pdf.text}
                for k, (_, pdf) in notes.items()]
    bundle = fhir_pas.build_request({**_order(case), "attempt": attempt},
                                    [{"source": "clinical_note", "quote": q} for q in quotes], attached, "letter",
                                    datetime.now(timezone.utc))
    return fhir_pas.read_request(bundle)


def test_pas_request_and_response_round_trip():
    case = _case("approved")
    request = _request(case, list(case["pa"]["criteria_quotes"].values()))
    assert request["cpts"] == [case["pa"]["cpt"]] and request["member_id"] == case["record"]["service_order"]["member_id"]
    assert [q["source"] for q in request["quotes"]] == ["clinical_note"] * len(case["pa"]["criteria_quotes"])
    review = payer.review_authorization(request, case["record"]["service_order"]["payer_name"], 1, TODAY)
    bundle = fhir_pas.build_response(request, review, datetime.now(timezone.utc))
    response = fhir_pas.read_response(bundle)
    assert response["decision"] == "approved" and response["review_action"] == "A1"
    assert response["auth_number"].startswith("PA-") and response["valid_from"] <= "2026-12-01" <= response["valid_to"]
    with pytest.raises(fhir_pas.FhirError):
        fhir_pas.read_response({"resourceType": "Bundle", "entry": []})


def test_payer_pends_then_denies_and_ignores_quotes_not_in_the_records():
    case = _case("missing_documentation")
    payer_name = case["record"]["service_order"]["payer_name"]
    c2 = case["pa"]["criteria_quotes"]["C2"]
    first = payer.review_authorization(_request(case, [c2]), payer_name, 1, TODAY)
    assert first["action"] == "A4" and first["missing"] == ["C1"] and "C1" in first["notes"][0]
    assert payer.review_authorization(_request(case, [c2], attempt=2), payer_name, 2, TODAY)["action"] == "A3"
    # Claiming the addendum's sentence without attaching the addendum doesn't count.
    c1 = case["pa"]["criteria_quotes"]["C1"]
    assert payer.review_authorization(_request(case, [c1, c2]), payer_name, 1, TODAY)["action"] == "A4"
    assert payer.review_authorization(_request(case, [c1, c2], with_addendum=True), payer_name, 1, TODAY)["action"] == "A1"


@pytest.mark.parametrize("case", PA_CASES, ids=[c["case_id"] for c in PA_CASES])
def test_requirement_and_criteria_match_the_answer_key(case):
    key = case["pa"]
    record, docs, notes = _packet(case)
    requirement = check_requirement(record, TODAY)
    assert (requirement["required"], requirement["eligible"]) == (key["required"], key["eligible"])
    if key["action"] in ("none", "ineligible"):
        return
    assert requirement["policy_id"] == key["policy_id"]
    assessment = assess_documentation(FakeLLM(), get_policy(key["policy_id"]), requirement["services"], notes)
    assert assessment.action == key["action"]
    assert {c["id"]: c["met"] for c in assessment.criteria} == key["criteria"]
    assert all(c["evidence"]["bbox"] for c in assessment.criteria if c["met"])
    if key.get("addendum"):
        _, _, with_addendum = _packet(case, with_addendum=True)
        again = assess_documentation(FakeLLM(), get_policy(key["policy_id"]), requirement["services"], with_addendum)
        assert again.action == "submit"
        assert next(c for c in again.criteria if c["id"] == "C1")["source"] == "clinical_note_2"


def test_request_letter_cites_the_addendum_and_drops_invented_facts():
    case = _case("missing_documentation")
    record, docs, notes = _packet(case, with_addendum=True)
    docs = {**docs, **notes}
    policy = get_policy("MP-1180")
    assessment = assess_documentation(FakeLLM(), policy, check_requirement(record, TODAY)["services"], notes).as_json()
    llm = FakeLLM(appeal_extra=[{"kind": "argument", "text": "An MRI on 02/02/2020 was normal.",
                                 "citations": [{"source": "clinical_note", "quote": "MRI of the lumbar spine requested"}]}])
    facts = {"request_number": "PAR-1", "patient": case["patient"], "scheduled_date": "2026-12-01"}
    draft = draft_request(llm, facts, assessment, docs, policy)
    sources = {c["source"] for p in draft.paragraphs for c in p["citations"]}
    assert "clinical_note_2" in sources and draft.stats["evidence_cited"] == draft.stats["evidence_required"] == 2
    assert draft.stats["invented_fact_paragraphs"] == 1


# --- through the API ---------------------------------------------------------------------------

def _wait(client, auth_id, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        auth = client.get(f"/api/authorizations/{auth_id}").json()
        if auth["status"] in statuses:
            return auth
        time.sleep(0.2)
    raise AssertionError(f"stuck in {auth['status']}: {[e['message'] for e in auth['events']][-6:]}")


def _load(client, scenario):
    login(client, "intake")
    r = client.post(f"/api/auth-samples/{_case(scenario)['case_id']}/load")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _edits(auth):
    return [{"source_index": i, "text": p["text"]} for i, p in enumerate(auth["draft"]["paragraphs"])]


def test_complete_request_is_approved_with_a_determination_letter(client):
    auth_id = _load(client, "approved")
    auth = _wait(client, auth_id, {"ready"})
    assert auth["requirement"]["required"] and auth["assessment"]["action"] == "submit"
    assert auth["draft"]["stats"]["evidence_cited"] == auth["draft"]["stats"]["evidence_required"] > 0
    r = client.post(f"/api/authorizations/{auth_id}/submit", json={"paragraphs": _edits(auth)})
    assert r.status_code == 200 and r.json()["status"] == "submitted" and r.json()["hours_left"] > 160
    auth = _wait(client, auth_id, {"approved"})
    assert auth["auth_number"].startswith("PA-") and auth["decision"]["review_action"] == "A1"
    assert auth["fhir_request"]["entry"][0]["resource"]["use"] == "preauthorization"
    letter = next(d for d in auth["documents"] if d["kind"] == "prior_authorization")
    assert auth["auth_number"] in parse_pdf_text(client, letter["id"])
    login(client, "biller")  # the audit trail is for billers
    assert client.get("/api/audit/verify").json()["ok"]


def parse_pdf_text(client, document_id) -> str:
    import pymupdf
    pdf = client.get(f"/api/documents/{document_id}/file").content
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_text()


def test_missing_documentation_loop_ends_in_approval(client):
    auth_id = _load(client, "missing_documentation")
    auth = _wait(client, auth_id, {"needs_documentation"})
    assert [m["id"] for m in auth["assessment"]["missing"]] == ["C1"] and auth["has_sample_addendum"]
    # Sending it now needs a reason, because documentation is known to be missing.
    assert client.post(f"/api/authorizations/{auth_id}/submit", json={"paragraphs": _edits(auth)}).status_code == 400
    r = client.post(f"/api/authorizations/{auth_id}/sample-addendum")
    assert r.status_code == 200 and r.json()["status"] == "checking"
    auth = _wait(client, auth_id, {"ready"})
    assert any(d["addendum"] for d in auth["documents"]) and not auth["has_sample_addendum"]
    client.post(f"/api/authorizations/{auth_id}/submit", json={"paragraphs": _edits(auth)})
    auth = _wait(client, auth_id, {"approved"})
    assert auth["attempts"] == 1


def test_unsupported_request_is_pended_then_denied(client):
    auth_id = _load(client, "not_necessary")
    auth = _wait(client, auth_id, {"needs_documentation"})
    assert auth["assessment"]["action"] == "do_not_submit"
    body = {"paragraphs": _edits(auth), "note": "Surgeon asked to submit; peer-to-peer requested"}
    assert client.post(f"/api/authorizations/{auth_id}/submit", json=body).status_code == 200
    auth = _wait(client, auth_id, {"pended"})
    assert auth["decision"]["review_action"] == "A4" and auth["decision"]["notes"]
    client.post(f"/api/authorizations/{auth_id}/submit", json={"paragraphs": _edits(auth)})
    auth = _wait(client, auth_id, {"denied"})
    assert auth["decision"]["review_action"] == "A3" and auth["attempts"] == 2


def test_no_auth_needed_and_inactive_coverage_stop_early(client):
    auth = _wait(client, _load(client, "not_required"), {"not_required"})
    assert not auth["requirement"]["required"] and auth["draft"] == {}
    auth = _wait(client, _load(client, "ineligible"), {"ineligible"})
    assert not auth["requirement"]["eligible"] and auth["requirement"]["eligibility"]["terminated_on"]
    r = client.post(f"/api/authorizations/{auth['id']}/withdraw", json={"note": "Patient has other coverage"})
    assert r.json()["status"] == "withdrawn"
    summary = client.get("/api/authorizations/summary").json()
    assert summary["not_required"] >= 1
