"""Medical coding: code sets, the E/M table, coding checks, the Coding Agent against the
answer keys, and the coder workflow through to a generated claim."""

import time

import pymupdf
import pytest

from app.agents.coding import code_encounter
from app.documents.pdf import parse_pdf
from app.verticals.healthcare.codesets import completions, dotted, is_billable, search_icd10
from app.verticals.healthcare.coding_rules import coding_edits, em_level
from app.verticals.healthcare.samples import list_cases
from tests.conftest import login
from tests.fake_llm import FakeLLM

ENC_CASES = list_cases("enc_")


def test_icd10_code_set_is_the_full_list():
    assert is_billable("M17.11") and is_billable("S61411A") and not is_billable("M17.1")  # M17.1 needs a 5th character
    assert dotted("s61411a") == "S61.411A"
    assert [c for c, _ in completions("M17.1")] == ["M17.10", "M17.11", "M17.12"]
    assert search_icd10("primary osteoarthritis left knee")[0]["code"] in ("M17.12", "M17.11", "M17.0")
    assert "M17.12" in [h["code"] for h in search_icd10("primary osteoarthritis left knee", 5)]


@pytest.mark.parametrize("status,mdm,minutes,expected", [
    ("established", ("low", "minimal", "low"), 22, "99213"),
    ("established", ("moderate", "low", "moderate"), None, "99214"),
    ("established", ("low", "low", "moderate"), 18, "99213"),        # two of three: low
    ("established", ("minimal", "minimal", "low"), 42, "99215"),     # time beats MDM
    ("new", ("moderate", "moderate", "moderate"), 48, "99204"),
    ("new", ("high", "high", "low"), None, "99205"),
])
def test_em_level_follows_the_2021_table(status, mdm, minutes, expected):
    elements = dict(zip(("problems", "data", "risk"), ({"level": m} for m in mdm)))
    assert em_level(status, elements, minutes)["code"] == expected


def _codes(edits):
    return {e["code"] for e in edits}


def test_coding_checks():
    note = "Corticosteroid injection into the left knee joint. Primary osteoarthritis of the left knee."
    assert {"ICD-INCOMPLETE"} <= _codes(coding_edits(["M17.1"], [{"code": "20610", "units": 1}], note, False))
    laterality = coding_edits(["M17.11"], [{"code": "20610", "units": 1}], note, False)
    assert next(e for e in laterality if e["code"] == "LATERALITY-MISMATCH")["fix"] == {"replace": "M17.12"}
    assert "UNSPECIFIED-CODE" in _codes(coding_edits(["M17.10"], [{"code": "20610", "units": 1}], note, False))
    visit = {"code": "99213", "modifiers": [], "units": 1}
    knee = {"code": "20610", "modifiers": [], "units": 1}
    assert "MODIFIER-25-MISSING" in _codes(coding_edits(["E11.9"], [visit, knee], note, True))
    assert "EM-BUNDLED" in _codes(coding_edits(["E11.9"], [visit, knee], note, False))
    assert "MODIFIER-25-UNSUPPORTED" in _codes(coding_edits(["E11.9"], [{**visit, "modifiers": ["25"]}, knee], note, False))
    assert not coding_edits(["E11.9", "M17.12"], [{**visit, "modifiers": ["25"]}, knee], note, True)
    assert "UNITS-EXCEEDED" in _codes(coding_edits(["M54.50"], [{"code": "97110", "units": 6}], "", False))
    assert {"CPT-UNKNOWN", "ICD-INVALID"} <= _codes(coding_edits(["Q99.99X"], [{"code": "99999"}], "", False))


@pytest.mark.parametrize("case", ENC_CASES, ids=[c["case_id"] for c in ENC_CASES])
def test_coding_agent_matches_the_answer_key(case):
    note = (1, parse_pdf(f"{case['path']}/clinical_note.pdf"))
    result = code_encounter(FakeLLM(), note)
    expected = case["coding"]["expected"]
    assert [d["code"] for d in result.diagnoses] == expected["icd"]
    assert [{"code": p["code"], "modifiers": p["modifiers"], "units": p["units"]} for p in result.procedures] == expected["cpt"]
    assert (result.em or {}).get("code") == expected["em"]
    assert result.stats["grounded"] == result.stats["quotes"]  # every code is pinned to the note
    assert result.edits == [] and result.stats["rejected_choices"] == 0


def test_codes_the_model_wasnt_offered_are_rejected():
    case = next(c for c in ENC_CASES if c["scenario"] == "knee_left_procedure")

    class Freelancer(FakeLLM):
        def complete_json(self, system, user, model=None):
            result = super().complete_json(system, user, model)
            if "diagnoses" in result.data and "candidates" in user:  # the selection step
                result.data["diagnoses"] = [{"index": 0, "code": "M17.9", "reason": "from memory"}]
            return result

    result = code_encounter(Freelancer(), (1, parse_pdf(f"{case['path']}/clinical_note.pdf")))
    assert result.stats["rejected_choices"] == 1 and result.diagnoses == []
    assert "NO-DIAGNOSIS" in _codes(result.edits)


# --- through the API ------------------------------------------------------------------------------

def _wait(client, enc_id, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        enc = client.get(f"/api/encounters/{enc_id}").json()
        if enc["status"] in statuses:
            return enc
        time.sleep(0.2)
    raise AssertionError(f"stuck in {enc['status']}: {[e['message'] for e in enc['events']][-5:]}")


def _load(client, scenario):
    login(client, "biller")
    case = next(c for c in ENC_CASES if c["scenario"] == scenario)
    r = client.post(f"/api/coding-samples/{case['case_id']}/load")
    assert r.status_code == 201, r.text
    return _wait(client, r.json()["id"], {"review"})


def _body(enc, reason=None):
    s = enc["suggestion"]
    return {"icd": [d["code"] for d in s["diagnoses"]],
            "cpt": [{"code": p["code"], "modifiers": p["modifiers"], "units": p["units"]} for p in s["procedures"]],
            "reason": reason}


def test_coder_approves_and_a_claim_is_generated(client):
    enc = _load(client, "knee_injection_mod25")
    assert [p["code"] + "-".join([""] + p["modifiers"]) for p in enc["suggestion"]["procedures"]] == ["99213-25", "20610"]
    assert enc["suggestion"]["em"]["basis"] in ("MDM", "time")
    r = client.post(f"/api/encounters/{enc['id']}/finalize", json=_body(enc))
    assert r.status_code == 200 and r.json()["status"] == "coded" and r.json()["final"]["agreed"]
    r = client.post(f"/api/encounters/{enc['id']}/claim")
    assert r.status_code == 200 and r.json()["status"] == "billed"
    claim = client.get(f"/api/claims/{r.json()['claim_id']}").json()
    assert claim["source"] == "coding" and {d["filename"] for d in claim["documents"]} == {
        "claim_form.pdf", "itemized_invoice.pdf", "insurance_card.pdf", "clinical_note.pdf"}
    form = next(d for d in claim["documents"] if d["filename"] == "claim_form.pdf")
    with pymupdf.open(stream=client.get(f"/api/documents/{form['id']}/file").content, filetype="pdf") as pdf:
        text = pdf[0].get_text()
    assert "M17.11" in text and "99213" in text and "25" in text and "$350.00" in text  # 145 + 205
    assert client.get("/api/audit/verify").json()["ok"]


def test_changes_need_a_reason_and_are_recorded(client):
    enc = _load(client, "knee_left_procedure")
    body = _body(enc)
    body["icd"] = ["M17.11"]  # the wrong knee
    check = client.post(f"/api/encounters/{enc['id']}/check", json=body).json()
    assert "LATERALITY-MISMATCH" in {e["code"] for e in check["edits"]}
    assert client.post(f"/api/encounters/{enc['id']}/finalize", json=body).status_code == 400
    body["icd"], body["reason"] = ["M17.12", "M25.562"], "Also documented left knee pain"
    r = client.post(f"/api/encounters/{enc['id']}/finalize", json=body)
    assert r.status_code == 200
    final = r.json()["final"]
    assert not final["agreed"] and {"kind": "icd", "change": "added", "code": "M25.562"} in final["changes"]
    bad = client.post(f"/api/encounters/{enc['id']}/finalize", json={**body, "icd": ["M17.1"]})
    assert bad.status_code == 409  # already coded
    assert client.get("/api/codes/icd10", params={"q": "M17.1"}).json()[0]["code"] == "M17.10"


@pytest.mark.parametrize("query,code", [
    ("laceration right palm", "S61.411A"),        # seen in evals: "palm" never found the hand code
    ("cut on right palm", "S61.411A"),
    ("ear wax right ear", "H61.21"),
    ("high blood pressure", "I10"),
])
def test_everyday_wording_still_finds_the_icd_code(query, code):
    assert code in [h["code"] for h in search_icd10(query, 12)]


def test_injury_encounter_versions_are_collapsed():
    codes = [h["code"] for h in search_icd10("laceration without foreign body right hand initial encounter", 12)]
    assert codes[0] == "S61.411A" and "S61.411D" not in codes and "S61.411S" not in codes
    assert "S61.412D" in [h["code"] for h in search_icd10("follow-up visit laceration left hand", 12)]
