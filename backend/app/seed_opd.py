"""Demo OPD: a duty roster, registered patients with some history, and today's queue.

All names, numbers and ABHA IDs are made up. Dates are relative to today (IST), so the demo
always has a live queue and a returning patient due a free follow-up. Seeded intake notes
come from the rules alone (no LLM call) and are marked demo.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.intake import run_intake
from app.frontdesk import _number, age_of, fee_for, on_duty, today
from app.models import Doctor, OpdPatient, OpdVisit
from app.verticals.opd_india.departments import DEPARTMENTS

MON_SAT = [0, 1, 2, 3, 4, 5]
DOCTORS = [  # name, department, qualification, room, fee, days, start, end, minutes, languages
    ("Dr. Anjali Mehta", "general_medicine", "MD (Medicine)", "101", 500, MON_SAT, "09:00", "14:00", 10, ["Hindi", "English", "Marathi"]),
    ("Dr. Rohan Kulkarni", "general_medicine", "MBBS, DNB (Family Medicine)", "102", 400, [0, 1, 2, 3, 4, 5, 6], "10:00", "16:00", 8, ["Hindi", "English", "Marathi"]),
    ("Dr. Farah Siddiqui", "paediatrics", "MD (Paediatrics)", "201", 600, MON_SAT, "09:30", "13:30", 12, ["Hindi", "English", "Urdu"]),
    ("Dr. Vikram Rao", "orthopaedics", "MS (Ortho)", "301", 800, [0, 2, 4, 5], "10:00", "14:00", 12, ["English", "Hindi", "Kannada"]),
    ("Dr. Sneha Iyer", "orthopaedics", "MS (Ortho), Fellowship Sports Medicine", "302", 700, [1, 3, 5], "14:00", "18:00", 12, ["English", "Tamil", "Hindi"]),
    ("Dr. Arjun Bhatia", "ent", "MS (ENT)", "204", 600, MON_SAT, "10:00", "13:00", 10, ["Hindi", "English", "Punjabi"]),
    ("Dr. Kavya Nair", "dermatology", "MD (Dermatology)", "205", 700, [0, 1, 3, 4], "11:00", "15:00", 10, ["English", "Malayalam", "Hindi"]),
    ("Dr. Priya Deshpande", "gynaecology", "MS (OBG)", "401", 800, MON_SAT, "09:00", "13:00", 15, ["Marathi", "Hindi", "English"]),
    ("Dr. Sanjay Gupta", "cardiology", "DM (Cardiology)", "402", 1000, [0, 2, 4], "11:00", "14:00", 15, ["Hindi", "English"]),
    ("Dr. Meera Joshi", "ophthalmology", "MS (Ophthalmology)", "206", 600, [1, 2, 3, 4, 5], "09:30", "13:30", 10, ["Hindi", "English", "Gujarati"]),
]
PATIENTS = [  # name, sex, age, phone, abha, city
    ("Ramesh Kumar", "M", 58, "9000010001", None, "Pune"),
    ("Sunita Kumar", "F", 54, "9000010001", None, "Pune"),
    ("Aarav Kumar", "M", 6, "9000010001", None, "Pune"),
    ("Mohammed Irfan", "M", 34, "9000010002", None, "Pune"),
    ("Lakshmi Narayanan", "F", 67, "9000010003", "91000000000003", "Pimpri"),
    ("Priya Sharma", "F", 28, "9000010004", None, "Pune"),
    ("Gurpreet Singh", "M", 45, "9000010005", None, "Hadapsar"),
    ("Fatima Begum", "F", 39, "9000010006", None, "Kondhwa"),
    ("Ankit Verma", "M", 22, "9000010007", None, "Pune"),
    ("Deepa Pillai", "F", 31, "9000010008", "91000000000008", "Aundh"),
    ("Suresh Yadav", "M", 61, "9000010009", None, "Wakad"),
    ("Neha Agarwal", "F", 26, "9000010010", None, "Baner"),
    ("Ishaan Agarwal", "M", 0.75, "9000010010", None, "Baner"),
]
HISTORY = [  # days ago, patient, doctor, complaint, English summary
    (3, 0, 0, "sugar badh gayi hai, bahut pyaas lagti hai", "High sugar readings and excess thirst."),
    (40, 4, 8, "dhadkan tez rehti hai, seedhi chadhne par saans phoolti hai", "Palpitations; breathless on stairs."),
    (60, 6, 3, "ghutne mein dard 2 mahine se", "Knee pain for 2 months."),
    (20, 7, 6, "haath aur gardan par khujli aur daane", "Itchy rash on hands and neck."),
    (10, 2, 2, "khansi aur halka bukhar", "Cough and mild fever."),
]
TODAY = [  # patient, doctor, complaint, English summary, status
    (10, 0, "khansi aur bukhar 4 din se", "Cough and fever for 4 days.", "done"),
    (8, 0, "pet mein dard aur ulti subah se", "Stomach pain and vomiting since morning.", "in_consult"),
    (9, 0, "headache for a week, worse in the evening", "Headache for a week.", "waiting"),
    (11, 1, "body ache and weakness for 3 days", "Body ache and weakness for 3 days.", "waiting"),
    (5, 7, "periods irregular for 3 months", "Irregular periods for 3 months.", "waiting"),
    (6, 3, "ghutne mein dard, seedhi chadhne mein dikkat", "Knee pain, worse on stairs.", "waiting"),
    (7, 6, "khujli abhi bhi hai, daane kam hue", "Itching continues; rash better.", "in_consult"),
    (12, 2, "9 mahine ka baccha, dast 2 din se", "Loose motions for 2 days (9-month-old).", "waiting"),
    (1, 5, "gale mein dard aur kharash", "Sore throat.", "waiting"),
]


def _intake(complaint: str, summary: str, patient: OpdPatient, doctor: Doctor, day: date) -> dict:
    note = run_intake(None, complaint, age_of(patient, day), patient.sex)
    note.update(summary_en=summary, department=doctor.department, department_name=DEPARTMENTS[doctor.department],
                department_source="demo", agent={"used": False, "error": None, "demo": True, "usage": None})
    return note


def seed_opd(db: Session) -> int:
    if db.scalar(select(func.count(Doctor.id))):
        return 0
    now, day = datetime.now(timezone.utc), today()
    doctors = []
    for name, dept, qual, room, fee, days, start, end, minutes, langs in DOCTORS:
        d = Doctor(name=name, department=dept, qualification=qual, room=room, fee=fee, days=days, opd_start=start,
                   opd_end=end, minutes_per_patient=minutes, languages=langs)
        db.add(d)
        doctors.append(d)
    patients = []
    registered = (day - timedelta(days=90)).isoformat()
    for i, (name, sex, age, phone, abha, city) in enumerate(PATIENTS):
        p = OpdPatient(uhid=f"UHID{100001 + i}", name=name, sex=sex, dob=None, age_years=age - 90 / 365.25 if age >= 1 else None,
                       phone=phone, abha=abha, city=city, registered_on=registered, registered_by="system")
        if age < 1:
            p.dob = (day - timedelta(days=int(age * 365.25))).isoformat()
        db.add(p)
        patients.append(p)
    db.flush()

    for days_ago, pi, di, complaint, summary in HISTORY:
        visit_day = day - timedelta(days=days_ago)
        p, d = patients[pi], doctors[di]
        db.add(OpdVisit(number=_number(db, visit_day), patient_id=p.id, doctor_id=d.id, visit_date=visit_day.isoformat(),
                        token=1, status="done", complaint=complaint, intake=_intake(complaint, summary, p, d, visit_day),
                        suggestion={}, fee=d.fee, fee_note="Consultation fee", payment_mode="upi", created_by="system",
                        created_at=now - timedelta(days=days_ago), booked_at=now - timedelta(days=days_ago),
                        done_at=now - timedelta(days=days_ago)))
        db.flush()

    tokens: dict[int, int] = {}
    for n, (pi, di, complaint, summary, status) in enumerate(TODAY):
        p, d = patients[pi], doctors[di]
        if not on_duty(d, day):
            continue
        tokens[d.id] = tokens.get(d.id, 0) + 1
        fee, fee_note = fee_for(db, p.id, d, day)
        booked = now - timedelta(minutes=(len(TODAY) - n) * 9)
        db.add(OpdVisit(number=_number(db, day), patient_id=p.id, doctor_id=d.id, visit_date=day.isoformat(),
                        token=tokens[d.id], status=status, complaint=complaint, intake=_intake(complaint, summary, p, d, day),
                        suggestion={}, fee=fee, fee_note=fee_note, payment_mode=("cash", "upi", "card")[n % 3] if fee else None,
                        created_by="intake@healthops.demo", created_at=booked, booked_at=booked,
                        called_at=booked + timedelta(minutes=5) if status != "waiting" else None,
                        done_at=booked + timedelta(minutes=12) if status == "done" else None))
        db.flush()
    db.commit()
    return len(patients)
