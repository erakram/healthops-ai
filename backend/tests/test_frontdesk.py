"""OPD front desk: red flags, the Intake Agent's guardrails, patient matching, doctor choice, fees and the queue."""

from datetime import date, timedelta

import pytest

from app import frontdesk as desk
from app.agents.intake import run_intake
from app.core.llm import LLMError, LLMResult
from app.db import SessionLocal
from app.models import Doctor, OpdPatient
from app.verticals.opd_india.departments import keyword_department
from app.verticals.opd_india.red_flags import check
from app.verticals.opd_india.text import contains
from tests.conftest import login
from tests.fake_llm import opd_cases

CASES = opd_cases()


class Scripted:
    """An LLM that gives one fixed answer."""

    def __init__(self, reply: dict | None = None, error: Exception | None = None):
        self.reply, self.error = reply or {}, error

    def complete_json(self, system, user, model=None):
        if self.error:
            raise self.error
        return LLMResult(self.reply, "scripted", 1, 1, 1)


# ---------- Red flags ----------

@pytest.mark.parametrize("case", [c for c in CASES if c["red_flag"]], ids=lambda c: c["id"])
def test_rules_alone_catch_every_red_flag(case):
    assert [f["id"] for f in check(case["complaint"], age=case["age"])] == [case["red_flag"]]


@pytest.mark.parametrize("case", [c for c in CASES if not c["red_flag"]], ids=lambda c: c["id"])
def test_no_false_alarms_on_routine_complaints(case):
    assert check(case["complaint"], age=case["age"]) == []


def test_chest_pain_needs_age_or_a_second_sign():
    assert check("chest pain after gym", age=24) == []
    assert [f["id"] for f in check("chest pain after gym", age=45)] == ["cardiac"]
    assert [f["id"] for f in check("chest pain and sweating", age=24)] == ["cardiac"]


def test_rules_also_read_the_agents_english_symptoms():
    """A spelling the rules don't know is caught through the symptom the agent extracted."""
    assert check("dil ke paas jakdan si", age=50) == []
    assert [f["id"] for f in check("dil ke paas jakdan si", ["chest heaviness", "sweating"], age=50)] == ["cardiac"]


@pytest.mark.parametrize("complaint", ["seene mai dard ho raha", "seene mei dard", "chhati m dard hai", "seena me dardh"])
def test_common_hinglish_spellings_of_chest_pain(complaint):
    assert [f["id"] for f in check(complaint, age=61)] == ["cardiac"]


def test_red_flags_and_departments_come_in_english_and_hindi():
    flag = check("seene mai dard ho raha", age=61)[0]
    assert flag["label"] == "Possible heart attack" and flag["label_hi"] == "दिल का दौरा हो सकता है" and flag["action_hi"]
    from app.verticals.opd_india.departments import DEPARTMENTS, DEPARTMENTS_HI
    from app.verticals.opd_india.red_flags import FLAGS
    assert set(DEPARTMENTS_HI) == set(DEPARTMENTS)
    assert all(f.label_hi and f.action_hi for f in FLAGS.values())


def test_hindi_from_the_agent_must_be_in_devanagari():
    note = run_intake(Scripted({"department": "ent", "summary_en": "Ear pain for 2 days.", "summary_hi": "2 दिन से कान में दर्द।",
                                "duration": "2 days", "duration_hi": "2 din",  # Latin letters: not shown as Hindi
                                "symptoms": [{"name": "ear pain", "name_hi": "कान में दर्द", "quote": "kaan mai dard"}]}),
                      "kaan mai dard 2 din se", 30, "M")
    assert note["summary_hi"] == "2 दिन से कान में दर्द।" and note["symptoms"][0]["name_hi"] == "कान में दर्द"
    assert note["duration_hi"] is None
    assert note["department_name_hi"] == "नाक, कान, गला"


def test_quotes_match_across_scripts_and_punctuation():
    assert contains("गले में दर्द और खराश, 3 दिन से", "गले में दर्द")
    assert contains("Kaan mein DARD!!", "kaan mein dard")
    assert not contains("kaan mein dard", "aankh mein dard")


# ---------- Intake Agent guardrails ----------

def test_agent_cannot_clear_a_red_flag():
    note = run_intake(Scripted({"department": "dermatology", "red_flag_signs": [], "symptoms": []}),
                      "seene mein dard aur pasina", 55, "M")
    assert [f["id"] for f in note["red_flags"]] == ["cardiac"]


def test_agent_can_add_a_red_flag_only_with_a_real_quote():
    complaint = "sar mein bahut tez dard, zindagi ka sabse bura"
    added = run_intake(Scripted({"department": "general_medicine",
                                 "red_flag_signs": [{"sign": "worst headache of life", "quote": "zindagi ka sabse bura"}]}),
                       complaint, 40, "F")
    assert [f["id"] for f in added["red_flags"]] == ["agent"]
    invented = run_intake(Scripted({"department": "general_medicine",
                                    "red_flag_signs": [{"sign": "x", "quote": "patient collapsed"}]}), complaint, 40, "F")
    assert invented["red_flags"] == []


def test_symptoms_without_a_real_quote_are_dropped():
    note = run_intake(Scripted({"department": "ent", "symptoms": [
        {"name": "ear pain", "quote": "kaan mein dard"}, {"name": "fever", "quote": "bukhar bhi hai"}]}),
        "kaan mein dard 2 din se", 30, "M")
    assert [s["name"] for s in note["symptoms"]] == ["ear pain"]
    assert note["dropped_symptoms"] == 1


def test_off_list_department_falls_back_to_keywords():
    note = run_intake(Scripted({"department": "neurosurgery"}), "aankh laal hai", 30, "M")
    assert (note["department"], note["department_source"]) == ("ophthalmology", "keywords")


def test_desk_works_when_the_model_is_down():
    note = run_intake(Scripted(error=LLMError("timeout")), "kaan mein dard", 30, "M")
    assert note["agent"]["used"] is False and note["department"] == "ent"
    assert run_intake(None, "galti se keetnashak pee liya", 19, "M")["red_flags"][0]["id"] == "poison"


def test_age_and_pregnancy_rules_override_the_agent():
    child = run_intake(Scripted({"department": "general_medicine"}), "bukhar aur khansi", 6, "M")
    assert (child["department"], child["department_source"]) == ("paediatrics", "rule")
    child_eye = run_intake(Scripted({"department": "ophthalmology"}), "aankh laal hai", 6, "M")
    assert child_eye["department"] == "ophthalmology"
    pregnant = run_intake(Scripted({"department": "general_medicine"}), "pregnant hoon, ulti ho rahi hai", 25, "F")
    assert pregnant["department"] == "gynaecology"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_answer_key_department_survives_the_rules(case, fake_llm):
    """With the answer key's department as the agent's answer, the rules never push it somewhere else."""
    note = run_intake(fake_llm, case["complaint"], case["age"], case["sex"])
    assert note["department"] == case["department"]


def test_keyword_rules_know_hindi_and_hinglish():
    assert keyword_department("ghutne mein dard") == "orthopaedics"
    assert keyword_department("गले में दर्द") == "ent"
    assert keyword_department("khujli aur daane") == "dermatology"


# ---------- Patients ----------

def test_phone_numbers_are_normalized():
    assert desk.normalize_phone("+91 98765 43210") == "9876543210"
    assert desk.normalize_phone("09876543210") == "9876543210"
    with pytest.raises(desk.DeskError):
        desk.normalize_phone("12345")
    with pytest.raises(desk.DeskError):
        desk.normalize_phone("5876543210")  # Indian mobiles start with 6-9


def _p(**kw) -> OpdPatient:
    base = dict(id=0, uhid="UHID1", name="Mohammed Irfan", sex="M", dob=None, age_years=34, phone="9000010002",
                abha=None, city=None, registered_on=desk.today().isoformat(), registered_by="x")
    return OpdPatient(**{**base, **kw})


def test_spelling_variants_on_the_same_phone_are_flagged():
    score, why = desk.match_score({"name": "Mohd. Irfan", "phone": "9000010002", "age": 35, "sex": "M"}, _p())
    assert score >= desk.POSSIBLE_MATCH and "same mobile number" in why


def test_family_members_sharing_a_phone_are_not_duplicates():
    score, _ = desk.match_score({"name": "Sunita Kumar", "phone": "9000010001", "age": 54, "sex": "F"},
                                _p(name="Ramesh Kumar", phone="9000010001", age_years=58))
    assert score < desk.POSSIBLE_MATCH
    score, _ = desk.match_score({"name": "Ramesh Kumar", "phone": "9000010001", "age": 30, "sex": "M"},
                                _p(name="Ramesh Kumar", phone="9000010001", age_years=58))  # son named after father
    assert score < desk.POSSIBLE_MATCH


def test_abha_decides_when_both_have_one():
    new = {"name": "Someone Else", "phone": "9999999999", "age": 20, "sex": "F", "abha": "91000000000003"}
    assert desk.match_score(new, _p(abha="91000000000003"))[0] == 1.0
    assert desk.match_score({**new, "name": "Mohammed Irfan", "phone": "9000010002"}, _p(abha="91000000000099"))[0] == 0.0


# ---------- Doctors and fees ----------

def test_suggested_doctors_are_always_on_duty(client):
    with SessionLocal() as db:
        patient = db.query(OpdPatient).first()
        for offset in range(7):
            day = desk.today() + timedelta(days=offset)
            for case in CASES:
                s = desk.suggest_doctors(db, patient, case["department"], day)
                assert s["options"], (case["id"], day)
                for option in s["options"]:
                    assert desk.on_duty(db.get(Doctor, option["id"]), day)


def test_free_follow_up_within_seven_days_of_a_paid_visit(client):
    with SessionLocal() as db:
        ramesh = db.query(OpdPatient).filter_by(name="Ramesh Kumar").one()
        mehta = db.query(Doctor).filter_by(name="Dr. Anjali Mehta").one()
        kulkarni = db.query(Doctor).filter_by(name="Dr. Rohan Kulkarni").one()
        today = desk.today()
        assert desk.fee_for(db, ramesh.id, mehta, today)[0] == 0  # seen 3 days ago
        assert desk.fee_for(db, ramesh.id, mehta, today + timedelta(days=5))[0] == mehta.fee  # 8 days later
        assert desk.fee_for(db, ramesh.id, kulkarni, today)[0] == kulkarni.fee  # different doctor


def test_returning_patient_gets_their_previous_doctor_first(client):
    with SessionLocal() as db:
        lakshmi = db.query(OpdPatient).filter_by(name="Lakshmi Narayanan").one()
        gupta = db.query(Doctor).filter_by(name="Dr. Sanjay Gupta").one()
        day = next(desk.today() + timedelta(days=i) for i in range(7) if desk.on_duty(gupta, desk.today() + timedelta(days=i)))
        s = desk.suggest_doctors(db, lakshmi, "cardiology", day)
        assert s["suggested_doctor_id"] == gupta.id and s["options"][0]["seen_before"]


def test_no_specialist_on_duty_falls_back_to_general_medicine(client):
    with SessionLocal() as db:
        patient = db.query(OpdPatient).first()
        joshi = db.query(Doctor).filter_by(name="Dr. Meera Joshi").one()
        day = next(desk.today() + timedelta(days=i) for i in range(7) if not desk.on_duty(joshi, desk.today() + timedelta(days=i)))
        s = desk.suggest_doctors(db, patient, "ophthalmology", day)
        assert "Dr. Meera Joshi is next in" in s["note"]
        assert {o["department"] for o in s["options"]} == {"general_medicine"}


# ---------- API flow ----------

def _register(client, **kw):
    body = {"name": "Kiran Patil", "sex": "F", "phone": "9123400001", "age": 41, **kw}
    return client.post("/api/frontdesk/patients", json=body).json()


def test_front_desk_flow(client):
    login(client, "intake")
    assert {p["name"] for p in client.get("/api/frontdesk/patients", params={"q": "9000010001"}).json()} == \
        {"Ramesh Kumar", "Sunita Kumar", "Aarav Kumar"}

    dup = _register(client, name="Mohd Irfan", sex="M", phone="9000010002", age=34)
    assert dup["registered"] is False and dup["possible_duplicates"][0]["patient"]["name"] == "Mohammed Irfan"
    new = _register(client)
    assert new["registered"] and new["patient"]["uhid"].startswith("UHID")
    pid = new["patient"]["id"]

    day = next(desk.today() + timedelta(days=i) for i in range(7)
               if (desk.today() + timedelta(days=i)).weekday() in (0, 1, 2, 3, 4, 5))
    visit = client.post("/api/frontdesk/intake", json={"patient_id": pid, "complaint": "kaan mein dard aur kaan se paani aa raha hai",
                                                        "visit_date": day.isoformat()}).json()
    assert visit["status"] == "triaged" and visit["intake"]["department"] == "ent"
    suggested = visit["suggestion"]["suggested_doctor_id"]

    other = next(o for o in client.get("/api/frontdesk/doctors", params={"day": day.isoformat()}).json()
                 if o["on_duty"] and o["id"] != suggested)
    r = client.post(f"/api/frontdesk/visits/{visit['id']}/book", json={"doctor_id": other["id"], "payment_mode": "cash"})
    assert r.status_code == 400 and "why" in r.json()["detail"]
    r = client.post(f"/api/frontdesk/visits/{visit['id']}/book", json={"doctor_id": suggested})
    assert r.status_code == 400 and "fee" in r.json()["detail"]
    booked = client.post(f"/api/frontdesk/visits/{visit['id']}/book", json={"doctor_id": suggested, "payment_mode": "upi"}).json()
    assert booked["status"] == "waiting" and booked["token"] >= 1 and booked["fee"] > 0

    slip = client.get(f"/api/frontdesk/visits/{visit['id']}/slip.pdf")
    assert slip.status_code == 200 and slip.content.startswith(b"%PDF")

    board = client.get("/api/frontdesk/queue", params={"day": day.isoformat()}).json()
    mine = next(d for d in board["doctors"] if d["id"] == suggested)
    assert any(v["id"] == visit["id"] for v in mine["visits"])

    assert client.post(f"/api/frontdesk/visits/{visit['id']}/call").json()["status"] == "in_consult"
    assert client.post(f"/api/frontdesk/visits/{visit['id']}/recall").status_code == 400
    assert client.post(f"/api/frontdesk/visits/{visit['id']}/done").json()["status"] == "done"


def test_red_flag_visit_cannot_get_a_routine_token(client):
    login(client, "intake")
    pid = _register(client, name="Harish Menon", sex="M", phone="9123400002", age=59)["patient"]["id"]
    visit = client.post("/api/frontdesk/intake", json={"patient_id": pid, "complaint": "seene mein dard aur pasina aa raha hai"}).json()
    assert visit["intake"]["red_flags"][0]["id"] == "cardiac"
    doctor = visit["suggestion"]["suggested_doctor_id"]
    if doctor is None:
        pytest.skip("no doctor on duty today")
    r = client.post(f"/api/frontdesk/visits/{visit['id']}/book", json={"doctor_id": doctor, "payment_mode": "cash"})
    assert r.status_code == 400 and "Emergency" in r.json()["detail"]
    assert client.post(f"/api/frontdesk/visits/{visit['id']}/emergency").json()["status"] == "emergency"


def test_calling_the_next_patient_finishes_the_current_one(client):
    login(client, "intake")
    day = desk.today()
    board = client.get("/api/frontdesk/queue").json()["doctors"]
    doctor = next((d for d in board if d["now"] and any(v["status"] == "waiting" for v in d["visits"])), None)
    if doctor is None:
        pytest.skip("no doctor with a patient in and one waiting today")
    current = doctor["now"]["id"]
    nxt = next(v for v in doctor["visits"] if v["status"] == "waiting")
    client.post(f"/api/frontdesk/visits/{nxt['id']}/call")
    assert client.get(f"/api/frontdesk/visits/{current}").json()["status"] == "done"
    assert day.isoformat() == client.get("/api/frontdesk/summary").json()["date"]


def test_billers_cannot_register_patients(client):
    login(client, "biller")
    assert client.post("/api/frontdesk/patients", json={"name": "A B", "sex": "M", "phone": "9123400003", "age": 30}).status_code == 403
    assert client.get("/api/frontdesk/queue").status_code == 200


def test_past_dates_and_far_future_are_refused(client):
    login(client, "intake")
    pid = _register(client, name="Rekha Joshi", sex="F", phone="9123400004", age=50)["patient"]["id"]
    for day in (date.today() - timedelta(days=2), desk.today() + timedelta(days=30)):
        r = client.post("/api/frontdesk/intake", json={"patient_id": pid, "complaint": "bukhar", "visit_date": day.isoformat()})
        assert r.status_code == 400
