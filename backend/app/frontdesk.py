"""OPD front desk: find or register the patient, intake, pick a doctor, issue a token, run the queue.

The Intake Agent reads the complaint (app/agents/intake.py). Everything else here is rules, so
every suggestion can be explained in one line:
  - duplicate patients: phone, name similarity, age, sex and ABHA scored; staff confirm,
  - doctor: on duty that day in the department; the doctor a returning patient saw before comes
    first, then the shortest queue; if no one in the department is on duty, General Medicine,
    with the day the specialist is next in,
  - fee: the doctor's fee, or free for a follow-up within 7 days of a paid visit to the same doctor,
  - token: the next number in that doctor's queue for the day.
"""

import difflib
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from app.agents.intake import run_intake
from app.core import audit
from app.core.events import publish
from app.core.llm import LLM
from app.models import Doctor, OpdPatient, OpdVisit, User
from app.verticals.opd_india.departments import DEPARTMENTS, DEPARTMENTS_HI

IST = ZoneInfo("Asia/Kolkata")
FREE_FOLLOW_UP_DAYS = 7
CONTINUITY_DAYS = 180
BOOK_AHEAD_DAYS = 7
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ACTIVE = ("waiting", "in_consult")
BOOKED = ("waiting", "in_consult", "done", "skipped")


class DeskError(ValueError):
    pass


def today() -> date:
    return datetime.now(IST).date()


# ---------- Patients ----------

def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise DeskError("Enter a 10-digit Indian mobile number.")
    return digits


def normalize_abha(abha: str | None) -> str | None:
    digits = re.sub(r"\D", "", abha or "")
    if not digits:
        return None
    if len(digits) != 14:
        raise DeskError("An ABHA number has 14 digits.")
    return digits


def age_of(p: OpdPatient, on: date | None = None) -> float | None:
    on = on or today()
    if p.dob:
        return round((on - date.fromisoformat(p.dob)).days / 365.25, 2)
    if p.age_years is not None:
        return round(p.age_years + (on - date.fromisoformat(p.registered_on)).days / 365.25, 2)
    return None


def age_label(age: float | None) -> str:
    if age is None:
        return "age unknown"
    if age < 1:
        return f"{max(int(age * 12), 0)} months"
    return f"{int(age)} years"


_TITLES = {"mr", "mrs", "ms", "miss", "smt", "shri", "sri", "kumari", "km", "dr", "master", "baby", "late"}
_SAME = {"mohd": "mohammed", "md": "mohammed", "mohd.": "mohammed", "muhammad": "mohammed", "mohammad": "mohammed",
         "mohamed": "mohammed", "kr": "kumar", "pd": "prasad", "chandra": "chander", "laxmi": "lakshmi"}


def name_key(name: str) -> str:
    words = re.sub(r"[^a-z ]", " ", name.lower()).split()
    words = [_SAME.get(w, w) for w in words if w not in _TITLES]
    return " ".join(sorted(words))


def name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, name_key(a), name_key(b)).ratio()


def match_score(new: dict, p: OpdPatient, on: date | None = None) -> tuple[float, list[str]]:
    """How likely an existing record is the same person as the details just typed (0-1), and why."""
    if new.get("abha") and p.abha:
        return (1.0, ["Same ABHA number"]) if new["abha"] == p.abha else (0.0, ["Different ABHA number"])
    score, why = 0.0, []
    if new.get("phone") == p.phone:
        score += 0.35
        why.append("same mobile number")
    sim = name_similarity(new["name"], p.name)
    if sim >= 0.7:
        score += 0.45 * sim
        why.append("same name" if sim > 0.97 else f"similar name ({p.name})")
    new_age = new.get("age")
    old_age = age_of(p, on)
    if new_age is not None and old_age is not None:
        gap = abs(new_age - old_age)
        if gap <= 2:
            score += 0.2
            why.append("same age")
        elif gap > 5:
            score -= 0.3
            why.append(f"age differs by {int(gap)} years")
    if new.get("sex") and new["sex"] != p.sex:
        score -= 0.5
        why.append("different sex")
    return round(max(score, 0.0), 2), why


POSSIBLE_MATCH = 0.6


def possible_duplicates(db: Session, new: dict) -> list[dict]:
    query = select(OpdPatient).where(or_(OpdPatient.phone == new["phone"],
                                         OpdPatient.abha == new["abha"] if new.get("abha") else false()))
    candidates = list(db.scalars(query))
    # Same person, different phone: look at everyone with a similar first word of the name.
    first = name_key(new["name"]).split()
    if first:
        like = [OpdPatient.name.ilike(f"%{w}%") for w in first if len(w) >= 3]
        if like:
            candidates += [p for p in db.scalars(select(OpdPatient).where(or_(*like)).limit(200)) if p not in candidates]
    out = []
    for p in candidates:
        score, why = match_score(new, p)
        if score >= POSSIBLE_MATCH:
            out.append({"patient": patient_json(p), "score": score, "why": why})
    return sorted(out, key=lambda m: -m["score"])


def patient_json(p: OpdPatient) -> dict:
    age = age_of(p)
    return {"id": p.id, "uhid": p.uhid, "name": p.name, "sex": p.sex, "dob": p.dob, "age": age, "age_label": age_label(age),
            "phone": p.phone, "abha": p.abha, "city": p.city, "registered_on": p.registered_on}


def search_patients(db: Session, q: str) -> list[OpdPatient]:
    q = q.strip()
    digits = re.sub(r"\D", "", q)
    if not q:
        return []
    if q.upper().startswith("UHID"):
        return list(db.scalars(select(OpdPatient).where(OpdPatient.uhid == q.upper().replace(" ", ""))))
    if len(digits) >= 4 and len(digits) >= len(q.replace(" ", "")) - 3:
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        return list(db.scalars(select(OpdPatient).where(or_(OpdPatient.phone.contains(digits), OpdPatient.abha == digits,
                                                               OpdPatient.uhid.contains(digits))).order_by(OpdPatient.name).limit(20)))
    words = [w for w in name_key(q).split() if len(w) >= 2] or [q.lower()]
    found = db.scalars(select(OpdPatient).where(*[OpdPatient.name.ilike(f"%{w}%") for w in words]).limit(50)).all()
    if not found:  # spelling variants: score everyone whose name shares a letter run
        pool = db.scalars(select(OpdPatient).where(OpdPatient.name.ilike(f"%{words[0][:3]}%")).limit(300)).all()
        found = [p for p in pool if name_similarity(q, p.name) >= 0.75]
    return sorted(found, key=lambda p: -name_similarity(q, p.name))[:20]


def next_uhid(db: Session) -> str:
    last = db.scalar(select(func.max(OpdPatient.id))) or 0
    return f"UHID{100001 + last}"


def register(db: Session, user: User, *, name: str, sex: str, phone: str, age: float | None = None, dob: str | None = None,
             abha: str | None = None, city: str | None = None, confirm_new: bool = False, on: date | None = None):
    """Register a patient. Returns (patient, None), or (None, possible duplicates) for staff to check first."""
    on = on or today()
    name = " ".join(name.split()).title() if name.islower() or name.isupper() else " ".join(name.split())
    if len(name) < 2:
        raise DeskError("Enter the patient's name.")
    if sex not in ("M", "F", "O"):
        raise DeskError("Sex must be M, F or O.")
    if dob:
        born = date.fromisoformat(dob)
        if born > on:
            raise DeskError("Date of birth is in the future.")
        age = (on - born).days / 365.25
    elif age is None:
        raise DeskError("Enter an age or a date of birth.")
    if age < 0 or age > 120:
        raise DeskError("Age must be between 0 and 120.")
    details = {"name": name, "sex": sex, "phone": normalize_phone(phone), "age": age, "abha": normalize_abha(abha)}
    if details["abha"] and db.scalar(select(OpdPatient).where(OpdPatient.abha == details["abha"])):
        confirm_new = False  # an ABHA number belongs to one person: never a second record
    matches = possible_duplicates(db, details)
    if matches and not confirm_new:
        return None, matches
    p = OpdPatient(uhid=next_uhid(db), name=name, sex=sex, dob=dob, age_years=None if dob else round(age, 2),
                   phone=details["phone"], abha=details["abha"], city=(city or "").strip() or None,
                   registered_on=on.isoformat(), registered_by=user.email)
    db.add(p)
    db.flush()
    audit.record(db, user.email, "patient.registered", "opd_patient", p.uhid,
                 {"possible_duplicates_overridden": [m["patient"]["uhid"] for m in matches]}, commit=False)
    db.commit()
    return p, None


# ---------- Doctors ----------

def on_duty(d: Doctor, day: date) -> bool:
    return day.weekday() in (d.days or [])


def next_duty_day(d: Doctor, after: date) -> date | None:
    for i in range(1, 8):
        day = after + timedelta(days=i)
        if on_duty(d, day):
            return day
    return None


def queue_counts(db: Session, day: date) -> dict[int, dict]:
    rows = db.execute(select(OpdVisit.doctor_id, OpdVisit.status, func.count())
                      .where(OpdVisit.visit_date == day.isoformat(), OpdVisit.doctor_id.is_not(None))
                      .group_by(OpdVisit.doctor_id, OpdVisit.status)).all()
    out: dict[int, dict] = {}
    for doctor_id, status, n in rows:
        out.setdefault(doctor_id, {})[status] = n
    return out


def wait_minutes(d: Doctor, counts: dict) -> int:
    return (counts.get("waiting", 0) + (1 if counts.get("in_consult") else 0)) * d.minutes_per_patient


def doctor_json(d: Doctor, day: date, counts: dict | None = None) -> dict:
    counts = counts or {}
    nxt = None if on_duty(d, day) else next_duty_day(d, day)
    return {"id": d.id, "name": d.name, "department": d.department, "department_name": DEPARTMENTS.get(d.department, d.department),
            "department_name_hi": DEPARTMENTS_HI.get(d.department), "qualification": d.qualification, "room": d.room, "fee": d.fee, "days": [DAY_NAMES[i][:3] for i in sorted(d.days)],
            "hours": f"{d.opd_start}-{d.opd_end}", "on_duty": on_duty(d, day), "next_on_duty": nxt.isoformat() if nxt else None,
            "waiting": counts.get("waiting", 0), "seen": counts.get("done", 0), "wait_minutes": wait_minutes(d, counts),
            "languages": d.languages}


def last_visits(db: Session, patient_id: int) -> list[OpdVisit]:
    return list(db.scalars(select(OpdVisit).where(OpdVisit.patient_id == patient_id, OpdVisit.status.in_(BOOKED))
                           .order_by(OpdVisit.visit_date.desc(), OpdVisit.id.desc()).limit(20)))


def fee_for(db: Session, patient_id: int, d: Doctor, day: date) -> tuple[float, str]:
    since = (day - timedelta(days=FREE_FOLLOW_UP_DAYS)).isoformat()
    paid = db.scalar(select(OpdVisit).where(OpdVisit.patient_id == patient_id, OpdVisit.doctor_id == d.id,
                                            OpdVisit.status.in_(BOOKED), OpdVisit.fee > 0,
                                            OpdVisit.visit_date >= since, OpdVisit.visit_date < day.isoformat())
                     .order_by(OpdVisit.visit_date.desc()).limit(1))
    if paid:
        return 0.0, f"Free follow-up: paid visit to {d.name} on {paid.visit_date}, within {FREE_FOLLOW_UP_DAYS} days"
    return d.fee, "Consultation fee"


def suggest_doctors(db: Session, patient: OpdPatient, department: str, day: date) -> dict:
    doctors = list(db.scalars(select(Doctor).order_by(Doctor.id)))
    counts = queue_counts(db, day)
    since = (day - timedelta(days=CONTINUITY_DAYS)).isoformat()
    seen_before = {v.doctor_id: v.visit_date for v in last_visits(db, patient.id) if v.visit_date >= since}
    in_dept = [d for d in doctors if d.department == department]
    duty = [d for d in in_dept if on_duty(d, day)]
    note = None
    if not duty:
        specialist = min((d for d in in_dept), key=lambda d: next_duty_day(d, day) or date.max, default=None)
        nxt = next_duty_day(specialist, day) if specialist else None
        note = (f"No {DEPARTMENTS[department]} doctor on duty on {DAY_NAMES[day.weekday()]}. "
                + (f"{specialist.name} is next in on {DAY_NAMES[nxt.weekday()]} {nxt.isoformat()}; " if nxt else "")
                + "General Medicine can see the patient today and refer.")
        duty = [d for d in doctors if d.department == "general_medicine" and on_duty(d, day)]

    options = []
    for d in duty:
        c = counts.get(d.id, {})
        fee, fee_note = fee_for(db, patient.id, d, day)
        reason = (f"Saw this patient on {seen_before[d.id]}" if d.id in seen_before
                  else f"Shortest wait in {DEPARTMENTS[d.department]}")
        options.append({**doctor_json(d, day, c), "seen_before": seen_before.get(d.id), "fee_due": fee, "fee_note": fee_note,
                        "reason": reason})
    options.sort(key=lambda o: (o["seen_before"] is None, o["wait_minutes"], o["id"]))
    if len(options) > 1 and options[0]["seen_before"] is None:
        options[0]["reason"] = f"Shortest wait in {options[0]['department_name']} (about {options[0]['wait_minutes']} min)"
    for o in options[1:]:
        o["reason"] = (f"Saw this patient on {o['seen_before']}" if o["seen_before"]
                       else f"About {o['wait_minutes']} min wait")
    return {"department": department, "department_name": DEPARTMENTS[department],
            "department_name_hi": DEPARTMENTS_HI[department], "note": note, "options": options,
            "suggested_doctor_id": options[0]["id"] if options else None}


# ---------- Visits ----------

def _visit_date(value: str | None) -> date:
    day = date.fromisoformat(value) if value else today()
    if day < today():
        raise DeskError("Can't book a date in the past.")
    if day > today() + timedelta(days=BOOK_AHEAD_DAYS):
        raise DeskError(f"OPD tokens can be booked up to {BOOK_AHEAD_DAYS} days ahead.")
    return day


def _number(db: Session, day: date) -> str:
    n = db.scalar(select(func.count(OpdVisit.id)).where(OpdVisit.visit_date == day.isoformat())) or 0
    return f"OPD-{day:%y%m%d}-{n + 1:03d}"


def triage(db: Session, llm: LLM | None, user: User, patient: OpdPatient, complaint: str,
           visit_date: str | None = None) -> OpdVisit:
    complaint = " ".join((complaint or "").split())
    if len(complaint) < 3:
        raise DeskError("Write what the patient says is wrong.")
    if len(complaint) > 1000:
        raise DeskError("Keep the complaint under 1,000 characters.")
    day = _visit_date(visit_date)
    note = run_intake(llm, complaint, age_of(patient, day), patient.sex)
    suggestion = suggest_doctors(db, patient, note["department"], day)
    visit = OpdVisit(number=_number(db, day), patient_id=patient.id, visit_date=day.isoformat(), complaint=complaint,
                     intake=note, suggestion=suggestion, status="triaged", created_by=user.email)
    db.add(visit)
    db.flush()
    audit.record(db, "intake-agent" if note["agent"]["used"] else "rules", "opd.triaged", "opd_visit", visit.number,
                 {"department": note["department"], "source": note["department_source"],
                  "red_flags": [f["id"] for f in note["red_flags"]]}, commit=False)
    db.commit()
    return visit


def book(db: Session, user: User, visit: OpdVisit, doctor_id: int, payment_mode: str | None,
         override_reason: str | None = None) -> OpdVisit:
    if visit.status != "triaged":
        raise DeskError("This visit already has a token or was sent to Emergency.")
    reason = (override_reason or "").strip()
    if visit.intake.get("red_flags") and len(reason) < 10:
        raise DeskError("Red flags found: send the patient to Emergency, or record that a casualty doctor cleared them for OPD.")
    doctor = db.get(Doctor, doctor_id)
    day = date.fromisoformat(visit.visit_date)
    if not doctor or not on_duty(doctor, day):
        raise DeskError("That doctor isn't on OPD duty that day.")
    if doctor_id != visit.suggestion.get("suggested_doctor_id") and len(reason) < 5 and not visit.intake.get("red_flags"):
        raise DeskError("Say why you're choosing a different doctor.")
    fee, fee_note = fee_for(db, visit.patient_id, doctor, day)
    if fee > 0 and payment_mode not in ("cash", "upi", "card"):
        raise DeskError("Choose how the fee was paid: cash, UPI or card.")
    token = (db.scalar(select(func.max(OpdVisit.token)).where(OpdVisit.doctor_id == doctor.id,
                                                              OpdVisit.visit_date == visit.visit_date)) or 0) + 1
    visit.doctor_id, visit.token, visit.status = doctor.id, token, "waiting"
    visit.fee, visit.fee_note, visit.payment_mode = fee, fee_note, payment_mode if fee > 0 else None
    visit.override_reason = reason or None
    visit.booked_at = datetime.now(timezone.utc)
    audit.record(db, user.email, "opd.token_issued", "opd_visit", visit.number,
                 {"doctor": doctor.name, "token": token, "fee": fee, "suggested_doctor_id": visit.suggestion.get("suggested_doctor_id"),
                  "override_reason": visit.override_reason}, commit=False)
    db.commit()
    publish("opd.updated", visit_id=visit.id, doctor_id=doctor.id, status=visit.status)
    return visit


def send_to_emergency(db: Session, user: User, visit: OpdVisit) -> OpdVisit:
    if visit.status != "triaged":
        raise DeskError("This visit already has a token.")
    visit.status = "emergency"
    audit.record(db, user.email, "opd.sent_to_emergency", "opd_visit", visit.number,
                 {"red_flags": [f["id"] for f in visit.intake.get("red_flags", [])]}, commit=False)
    db.commit()
    publish("opd.updated", visit_id=visit.id, status=visit.status)
    return visit


MOVES = {"call": ("waiting",), "done": ("in_consult",), "skip": ("waiting", "in_consult"), "recall": ("skipped",),
         "cancel": ("waiting", "skipped")}


def move(db: Session, user: User, visit: OpdVisit, action: str) -> OpdVisit:
    if action not in MOVES:
        raise DeskError("Unknown queue action.")
    if visit.status not in MOVES[action]:
        raise DeskError(f"Can't {action} a visit that is {visit.status.replace('_', ' ')}.")
    now = datetime.now(timezone.utc)
    if action == "call":  # the doctor takes the next patient: whoever was in with them is done
        for other in db.scalars(select(OpdVisit).where(OpdVisit.doctor_id == visit.doctor_id, OpdVisit.status == "in_consult",
                                                       OpdVisit.visit_date == visit.visit_date)):
            other.status, other.done_at = "done", now
        visit.status, visit.called_at = "in_consult", now
    elif action == "done":
        visit.status, visit.done_at = "done", now
    elif action == "skip":
        visit.status = "skipped"
    elif action == "recall":
        visit.status = "waiting"
    elif action == "cancel":
        visit.status = "cancelled"
    audit.record(db, user.email, f"opd.{action}", "opd_visit", visit.number, {"token": visit.token}, commit=False)
    db.commit()
    publish("opd.updated", visit_id=visit.id, doctor_id=visit.doctor_id, status=visit.status)
    return visit


def visit_json(v: OpdVisit) -> dict:
    return {"id": v.id, "number": v.number, "visit_date": v.visit_date, "token": v.token, "status": v.status,
            "complaint": v.complaint, "intake": v.intake, "suggestion": v.suggestion, "override_reason": v.override_reason,
            "fee": v.fee, "fee_note": v.fee_note, "payment_mode": v.payment_mode, "created_by": v.created_by,
            "patient": patient_json(v.patient),
            "doctor": {"id": v.doctor.id, "name": v.doctor.name, "room": v.doctor.room,
                       "department_name": DEPARTMENTS.get(v.doctor.department),
                       "department_name_hi": DEPARTMENTS_HI.get(v.doctor.department)} if v.doctor else None,
            "booked_at": v.booked_at.isoformat() if v.booked_at else None,
            "called_at": v.called_at.isoformat() if v.called_at else None}


def queue(db: Session, day: date) -> list[dict]:
    order = list(DEPARTMENTS)
    doctors = sorted(db.scalars(select(Doctor)), key=lambda d: (order.index(d.department) if d.department in order else 99, d.id))
    visits = list(db.scalars(select(OpdVisit).where(OpdVisit.visit_date == day.isoformat(), OpdVisit.doctor_id.is_not(None))
                             .order_by(OpdVisit.token)))
    counts = queue_counts(db, day)
    out = []
    for d in doctors:
        mine = [v for v in visits if v.doctor_id == d.id]
        if not on_duty(d, day) and not mine:
            continue
        out.append({**doctor_json(d, day, counts.get(d.id, {})),
                    "now": next(({"id": v.id, "token": v.token, "name": v.patient.name} for v in mine if v.status == "in_consult"), None),
                    "visits": [{"id": v.id, "token": v.token, "status": v.status, "name": v.patient.name,
                                "age_label": age_label(age_of(v.patient, day)), "sex": v.patient.sex,
                                "summary": v.intake.get("summary_en") or v.complaint,
                                "summary_hi": v.intake.get("summary_hi"), "fee": v.fee}
                               for v in mine if v.status != "cancelled"]})
    return out


def summary(db: Session, day: date) -> dict:
    iso = day.isoformat()
    visits = list(db.scalars(select(OpdVisit).where(OpdVisit.visit_date == iso)))
    booked = [v for v in visits if v.status in BOOKED]
    return {"date": iso, "day": DAY_NAMES[day.weekday()],
            "tokens": len(booked), "waiting": sum(v.status == "waiting" for v in visits),
            "in_consult": sum(v.status == "in_consult" for v in visits), "seen": sum(v.status == "done" for v in visits),
            "emergencies": sum(v.status == "emergency" for v in visits),
            "collected": round(sum(v.fee for v in booked), 2), "free_follow_ups": sum(1 for v in booked if v.fee == 0),
            "new_patients": db.scalar(select(func.count(OpdPatient.id)).where(OpdPatient.registered_on == iso)) or 0,
            "doctors_on_duty": sum(1 for d in db.scalars(select(Doctor)) if on_duty(d, day))}
