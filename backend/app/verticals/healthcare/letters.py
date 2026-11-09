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
    c = canvas.Canvas(path, pagesize=letter, invariant=1)
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


# --- claim packet generated from coded encounters ------------------------------------------------

def _header(c, title: str, subtitle: str, color: str) -> None:
    w, h = letter
    c.setFillColor(colors.HexColor(color))
    c.rect(0, h - 70, w, 70, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(0.6 * inch, h - 40, title)
    c.setFont("Helvetica", 9)
    c.drawString(0.6 * inch, h - 56, subtitle)
    c.setFillColor(colors.black)


def _box(c, x, y, w, label, value, h=34) -> None:
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.rect(x, y, w, h, fill=0)
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(x + 4, y + h - 10, label)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    c.drawString(x + 4, y + 8, value)


def _table(c, x, y, widths, rows, fill) -> float:
    for r, row in enumerate(rows):
        cx = x
        if r == 0:
            c.setFillColor(colors.HexColor(fill))
            c.rect(x, y - 20, sum(widths), 20, fill=1, stroke=0)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 8.5)
        else:
            c.setFont("Helvetica", 9)
        for width, cell in zip(widths, row):
            c.drawString(cx + 4, y - 14, str(cell))
            cx += width
        c.setStrokeColor(colors.HexColor("#cbd5e1"))
        c.line(x, y - 20, x + sum(widths), y - 20)
        y -= 20
    return y


def _money(v: float) -> str:
    return f"${v:,.2f}"


def claim_form(path: str, d: dict) -> None:
    """d: claim_number, payer_name, patient_name, patient_dob, member_id, provider_name, provider_npi,
    service_date (MM/DD/YYYY), diagnosis_codes, prior_auth_number, line_items [{cpt, modifiers, description, units, charge}]"""
    c = canvas.Canvas(path, pagesize=letter, invariant=1)
    _header(c, "HEALTH INSURANCE CLAIM FORM", "Professional services claim - generated from coded encounter", "#0f766e")
    w, h = letter
    x, y, full = 0.6 * inch, h - 120, w - 1.2 * inch
    _box(c, x, y, full / 2, "CLAIM NUMBER", d["claim_number"])
    _box(c, x + full / 2, y, full / 2, "PAYER", d["payer_name"])
    y -= 40
    _box(c, x, y, full / 2, "Patient Name", d["patient_name"])
    _box(c, x + full / 2, y, full / 4, "Date of Birth", d["patient_dob"])
    _box(c, x + 3 * full / 4, y, full / 4, "Member ID", d["member_id"])
    y -= 40
    _box(c, x, y, full / 2, "33. BILLING PROVIDER", d["provider_name"])
    _box(c, x + full / 2, y, full / 4, "33a. NPI", d["provider_npi"])
    _box(c, x + 3 * full / 4, y, full / 4, "24A. DATE OF SERVICE", d["service_date"])
    y -= 40
    _box(c, x, y, full / 2, "21. DIAGNOSIS CODES (ICD-10)", "   ".join(d["diagnosis_codes"]))
    _box(c, x + full / 2, y, full / 2, "23. PRIOR AUTHORIZATION NUMBER", d.get("prior_auth_number") or "")
    y -= 30
    rows = [["CPT", "MOD", "DESCRIPTION", "UNITS", "CHARGE"]]
    rows += [[li["cpt"], " ".join(li.get("modifiers") or []), li["description"], str(li["units"]), _money(li["charge"])]
             for li in d["line_items"]]
    y = _table(c, x, y, [55, 40, 275, 45, full - 415], rows, "#e2e8f0")
    total = sum(li["charge"] for li in d["line_items"])
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + full - 200, y - 26, f"28. TOTAL CHARGE  {_money(total)}")
    c.setFont("Helvetica", 7)
    c.drawString(x, 0.6 * inch, "Synthetic document generated by HealthOps AI from a coded encounter. All identifiers are fictitious.")
    c.save()


def itemized_invoice(path: str, d: dict) -> None:
    c = canvas.Canvas(path, pagesize=letter, invariant=1)
    _header(c, d["provider_name"], "Itemized statement of services", "#1e3a8a")
    w, h = letter
    x, y = 0.6 * inch, h - 110
    c.setFont("Helvetica", 10)
    for line in (f"Invoice #: {d['invoice_number']}", f"Patient: {d['patient_name']}",
                 f"Service date: {d['service_date']}", f"Insurance: {d['payer_name']}"):
        c.drawString(x, y, line)
        y -= 15
    rows = [["CODE", "SERVICE", "QTY", "AMOUNT"]]
    rows += [[li["cpt"], li["description"], str(li["units"]), _money(li["charge"])] for li in d["line_items"]]
    y = _table(c, x, y - 10, [60, 300, 50, 100], rows, "#dbeafe")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 300, y - 24, f"Amount due: {_money(sum(li['charge'] for li in d['line_items']))}")
    c.setFont("Helvetica", 7)
    c.drawString(x, 0.6 * inch, "Synthetic document generated by HealthOps AI from a coded encounter. All identifiers are fictitious.")
    c.save()


STAGE_TITLES = {"statement": "Patient statement", "reminder": "Payment reminder", "final_notice": "Final notice"}


def patient_statement(path: str, d: dict) -> None:
    """d: provider_name, number, stage, patient_name, issued_on, due_on (MM/DD/YYYY), items [{service_date, description,
    reason, amount}], total, paid, plan_text (optional)"""
    c = canvas.Canvas(path, pagesize=letter, invariant=1)
    color = {"statement": "#0f766e", "reminder": "#b45309", "final_notice": "#b91c1c"}[d["stage"]]
    _header(c, d["provider_name"], STAGE_TITLES[d["stage"]], color)
    w, h = letter
    x, y = 0.6 * inch, h - 110
    c.setFont("Helvetica", 10)
    for line in (f"Statement: {d['number']}", f"Patient: {d['patient_name']}", f"Statement date: {d['issued_on']}",
                 f"Please pay by: {d['due_on']}"):
        c.drawString(x, y, line)
        y -= 15
    rows = [["DATE", "SERVICE", "WHY YOU OWE THIS", "AMOUNT"]]
    rows += [[i["service_date"], i["description"][:28], i["reason"][:52], _money(i["amount"])] for i in d["items"]]
    y = _table(c, x, y - 10, [70, 140, 220, 70], rows, "#e2e8f0")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 290, y - 24, f"Balance due: {_money(round(d['total'] - d['paid'], 2))}")
    c.setFont("Helvetica", 9)
    y -= 50
    for line in ([d["plan_text"]] if d.get("plan_text") else []) + [
            "Your insurance has already paid its share. This is the part your plan says you pay.",
            "Questions or need a payment plan? Call the billing office. Financial assistance is available."]:
        c.drawString(x, y, line)
        y -= 14
    c.setFont("Helvetica", 7)
    c.drawString(x, 0.6 * inch, "Synthetic document generated by HealthOps AI. All names and identifiers are fictitious.")
    c.save()
