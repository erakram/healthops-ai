"""OPD front desk API: patients, intake, tokens and the queue board."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import frontdesk as desk
from app.config import settings
from app.core.auth import current_user, require_role
from app.db import get_db
from app.models import Doctor, OpdPatient, OpdVisit, User
from app.verticals.opd_india.slip import opd_slip

router = APIRouter(prefix="/api/frontdesk", tags=["OPD front desk"])
staff = require_role("intake")


def _day(value: str | None) -> date:
    try:
        return date.fromisoformat(value) if value else desk.today()
    except ValueError:
        raise HTTPException(400, "Dates are YYYY-MM-DD")


def _patient(db: Session, patient_id: int) -> OpdPatient:
    p = db.get(OpdPatient, patient_id)
    if not p:
        raise HTTPException(404, "Patient not found")
    return p


def _visit(db: Session, visit_id: int) -> OpdVisit:
    v = db.get(OpdVisit, visit_id)
    if not v:
        raise HTTPException(404, "Visit not found")
    return v


@router.get("/summary")
def summary(day: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return desk.summary(db, _day(day))


@router.get("/doctors")
def doctors(day: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    d = _day(day)
    counts = desk.queue_counts(db, d)
    return [desk.doctor_json(doc, d, counts.get(doc.id, {})) for doc in db.scalars(select(Doctor).order_by(Doctor.department, Doctor.id))]


@router.get("/patients")
def search(q: str = "", db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [desk.patient_json(p) for p in desk.search_patients(db, q)]


class NewPatient(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    sex: Literal["M", "F", "O"]
    phone: str = Field(max_length=20)
    age: float | None = Field(default=None, ge=0, le=120)
    dob: str | None = None
    abha: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=100)
    confirm_new: bool = False


@router.post("/patients")
def register(body: NewPatient, db: Session = Depends(get_db), user: User = Depends(staff)):
    try:
        patient, matches = desk.register(db, user, **body.model_dump())
    except (desk.DeskError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    if matches:
        return {"registered": False, "possible_duplicates": matches}
    return {"registered": True, "patient": desk.patient_json(patient)}


@router.get("/patients/{patient_id}")
def patient(patient_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    p = _patient(db, patient_id)
    visits = db.scalars(select(OpdVisit).where(OpdVisit.patient_id == p.id, OpdVisit.status != "triaged")
                        .order_by(OpdVisit.visit_date.desc(), OpdVisit.id.desc()).limit(20)).all()
    family = db.scalars(select(OpdPatient).where(OpdPatient.phone == p.phone, OpdPatient.id != p.id)).all()
    return {**desk.patient_json(p), "family": [desk.patient_json(f) for f in family],
            "visits": [{"id": v.id, "number": v.number, "visit_date": v.visit_date, "status": v.status, "token": v.token,
                        "doctor": v.doctor.name if v.doctor else None,
                        "department_name": v.intake.get("department_name"),
                        "summary": v.intake.get("summary_en") or v.complaint, "fee": v.fee} for v in visits]}


class Intake(BaseModel):
    patient_id: int
    complaint: str = Field(min_length=3, max_length=1000)
    visit_date: str | None = None


@router.post("/intake")
def intake(body: Intake, request: Request, db: Session = Depends(get_db), user: User = Depends(staff)):
    p = _patient(db, body.patient_id)
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        worker = getattr(request.app.state, "worker", None)
        llm = getattr(worker, "llm", None)
    try:
        v = desk.triage(db, llm, user, p, body.complaint, body.visit_date)
    except (desk.DeskError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return desk.visit_json(v)


@router.get("/visits/{visit_id}")
def visit(visit_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return desk.visit_json(_visit(db, visit_id))


class Booking(BaseModel):
    doctor_id: int
    payment_mode: Literal["cash", "upi", "card"] | None = None
    override_reason: str | None = Field(default=None, max_length=500)


@router.post("/visits/{visit_id}/book")
def book(visit_id: int, body: Booking, db: Session = Depends(get_db), user: User = Depends(staff)):
    try:
        v = desk.book(db, user, _visit(db, visit_id), body.doctor_id, body.payment_mode, body.override_reason)
    except desk.DeskError as exc:
        raise HTTPException(400, str(exc))
    return desk.visit_json(v)


@router.post("/visits/{visit_id}/emergency")
def emergency(visit_id: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    try:
        return desk.visit_json(desk.send_to_emergency(db, user, _visit(db, visit_id)))
    except desk.DeskError as exc:
        raise HTTPException(400, str(exc))


@router.post("/visits/{visit_id}/{action}")
def move(visit_id: int, action: Literal["call", "done", "skip", "recall", "cancel"], db: Session = Depends(get_db),
         user: User = Depends(staff)):
    try:
        return desk.visit_json(desk.move(db, user, _visit(db, visit_id), action))
    except desk.DeskError as exc:
        raise HTTPException(400, str(exc))


@router.get("/visits/{visit_id}/slip.pdf")
def slip(visit_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    v = _visit(db, visit_id)
    if not v.doctor or v.token is None:
        raise HTTPException(400, "No token issued for this visit")
    day = date.fromisoformat(v.visit_date)
    counts = desk.queue_counts(db, day).get(v.doctor_id, {})
    ahead = len(db.scalars(select(OpdVisit.id).where(OpdVisit.doctor_id == v.doctor_id, OpdVisit.visit_date == v.visit_date,
                                                     OpdVisit.status == "waiting", OpdVisit.token < v.token)).all())
    wait = (ahead + (1 if counts.get("in_consult") else 0)) * v.doctor.minutes_per_patient if v.status == "waiting" else None
    pdf = opd_slip(hospital=settings.hospital_name, number=v.number, token=v.token, visit_date=v.visit_date,
                   patient=desk.patient_json(v.patient), doctor=desk.visit_json(v)["doctor"], fee=v.fee, fee_note=v.fee_note,
                   payment_mode=v.payment_mode, summary=v.intake.get("summary_en"), wait_minutes=wait)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{v.number}.pdf"'})


@router.get("/queue")
def queue(day: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    d = _day(day)
    return {"date": d.isoformat(), "doctors": desk.queue(db, d)}
