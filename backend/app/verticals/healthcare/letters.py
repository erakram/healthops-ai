"""PDFs the platform produces itself.

The determination letter uses the same layout as the prior authorization documents in the
sample claim packets, so when it's attached to a claim later, the Document Agent reads it
like any other authorization.
"""

from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


def _us(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%m/%d/%Y")


def determination_letter(path: str, *, payer: str, auth_number: str, member_id: str, patient: str,
                         valid_from: str, valid_to: str, services: list[dict], request_number: str) -> None:
    c = canvas.Canvas(path, pagesize=letter)
    w, h = letter
    c.setFillColor(colors.HexColor("#7c3aed"))
    c.rect(0, h - 70, w, 70, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(0.6 * inch, h - 40, f"{payer} - Prior Authorization")
    c.setFont("Helvetica", 9)
    c.drawString(0.6 * inch, h - 56, "Utilization management determination")
    c.setFillColor(colors.black)
    x, y = 0.6 * inch, h - 110
    c.setFont("Helvetica", 10)
    for line in (f"Authorization number: {auth_number}", f"Member ID: {member_id}", f"Member: {patient}",
                 "Determination: APPROVED", f"Valid from: {_us(valid_from)}", f"Valid to: {_us(valid_to)}",
                 f"Request reference: {request_number}"):
        c.drawString(x, y, line)
        y -= 15
    y -= 10
    c.setFillColor(colors.HexColor("#ede9fe"))
    c.rect(x, y - 20, 480, 20, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(x + 4, y - 14, "APPROVED CPT")
    c.drawString(x + 104, y - 14, "DESCRIPTION")
    c.setFont("Helvetica", 9)
    for s in services:
        y -= 20
        c.drawString(x + 4, y - 14, s["cpt"])
        c.drawString(x + 104, y - 14, s.get("description") or "")
    c.setFont("Helvetica", 7)
    c.drawString(x, 0.6 * inch, "Synthetic document generated for HealthOps AI by the mock payer. Not valid for coverage.")
    c.save()
