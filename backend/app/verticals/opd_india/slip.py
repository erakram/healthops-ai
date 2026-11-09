"""The OPD slip the patient carries to the doctor's room: token, doctor, room, fee.

Built in memory (nothing stored). The standard PDF fonts have no rupee sign or Devanagari, so
amounts print as "Rs." and only the English summary of the complaint is shown."""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas


def opd_slip(*, hospital: str, number: str, token: int, visit_date: str, patient: dict, doctor: dict,
             fee: float, fee_note: str | None, payment_mode: str | None, summary: str | None, wait_minutes: int | None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A6, invariant=1)
    w, h = A6
    c.setFillColor(colors.HexColor("#0f766e"))
    c.rect(0, h - 22 * mm, w, 22 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(6 * mm, h - 10 * mm, hospital)
    c.setFont("Helvetica", 8)
    c.drawString(6 * mm, h - 16 * mm, f"OPD slip  {number}  {visit_date}")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 8)
    c.drawString(6 * mm, h - 30 * mm, "TOKEN")
    c.setFont("Helvetica-Bold", 40)
    c.drawString(6 * mm, h - 46 * mm, str(token))
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(w - 6 * mm, h - 32 * mm, f"Room {doctor['room']}")
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 6 * mm, h - 38 * mm, doctor["name"])
    c.drawRightString(w - 6 * mm, h - 43 * mm, doctor["department_name"])
    if wait_minutes is not None:
        c.setFont("Helvetica", 8)
        c.drawRightString(w - 6 * mm, h - 48 * mm, f"Estimated wait: about {wait_minutes} min")
    y = h - 58 * mm
    width = w - 12 * mm
    fee_line = (f"Fee: Rs. {fee:,.0f} ({payment_mode.upper()})" if fee and payment_mode
                else f"Fee: Rs. {fee:,.0f}" if fee else f"Fee: none - {fee_note or 'free follow-up'}")
    lines = [("Helvetica", 9, f"Patient: {patient['name']}  ({patient['sex']}, {patient['age_label']})"),
             ("Helvetica", 9, f"UHID: {patient['uhid']}"),
             ("Helvetica", 9, f"Mobile: {patient['phone'][:2]}XXXXXX{patient['phone'][-2:]}"),
             ("Helvetica", 9, fee_line)]
    if summary:
        lines.append(("Helvetica-Oblique", 8, f"Complaint: {summary}"))
    for font, size, text in lines:
        c.setFont(font, size)
        for part in simpleSplit(text, font, size, width)[:3]:
            c.drawString(6 * mm, y, part)
            y -= size * 0.5 * mm + 1.2 * mm
        y -= 0.8 * mm
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.grey)
    c.drawString(6 * mm, 8 * mm, "Please wait near the room until your token is called. Synthetic demo data.")
    c.showPage()
    c.save()
    return buf.getvalue()
