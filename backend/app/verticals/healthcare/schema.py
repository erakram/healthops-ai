"""What the healthcare pack reads from each document type, and how values are normalised.

Everything vertical-specific lives in this package (document types, fields, rules,
prompts, reference data). The core (workflow, audit, agents' plumbing) doesn't know
what a claim form is.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    type: str = "text"  # text | date | money | npi
    required: bool = True


DOCUMENT_TYPES: dict[str, dict] = {
    "claim_form": {
        "label": "Claim form",
        "fields": [
            FieldSpec("claim_number", "Claim number"),
            FieldSpec("patient_name", "Patient name"),
            FieldSpec("patient_dob", "Patient date of birth", "date"),
            FieldSpec("member_id", "Member ID"),
            FieldSpec("payer_name", "Payer"),
            FieldSpec("provider_name", "Billing provider"),
            FieldSpec("provider_npi", "Provider NPI", "npi"),
            FieldSpec("service_date", "Date of service", "date"),
            FieldSpec("total_charge", "Total charge", "money"),
            FieldSpec("prior_auth_number", "Prior authorization number", required=False),
        ],
        "lists": {"line_items": "Billed services (CPT code, description, units, charge)",
                  "diagnosis_codes": "ICD-10 diagnosis codes"},
    },
    "invoice": {
        "label": "Itemized invoice",
        "fields": [
            FieldSpec("invoice_number", "Invoice number"),
            FieldSpec("patient_name", "Patient name"),
            FieldSpec("service_date", "Date of service", "date"),
            FieldSpec("total_amount", "Invoice total", "money"),
        ],
        "lists": {"line_items": "Invoice lines (CPT code, description, units, charge)"},
    },
    "insurance_card": {
        "label": "Insurance card",
        "fields": [
            FieldSpec("member_name", "Member name"),
            FieldSpec("member_id", "Member ID"),
            FieldSpec("payer_name", "Payer"),
            FieldSpec("group_number", "Group number", required=False),
            FieldSpec("coverage_start", "Coverage start", "date"),
            FieldSpec("coverage_end", "Coverage end", "date", required=False),
        ],
        "lists": {},
    },
    "clinical_note": {
        "label": "Clinical note",
        "fields": [
            FieldSpec("patient_name", "Patient name"),
            FieldSpec("patient_dob", "Patient date of birth", "date", required=False),
            FieldSpec("service_date", "Date of service", "date"),
            FieldSpec("summary", "Services documented", required=False),
        ],
        "lists": {},
    },
    "prior_authorization": {
        "label": "Prior authorization",
        "fields": [
            FieldSpec("auth_number", "Authorization number"),
            FieldSpec("member_id", "Member ID"),
            FieldSpec("valid_from", "Valid from", "date"),
            FieldSpec("valid_to", "Valid to", "date"),
        ],
        "lists": {"approved_cpt_codes": "Approved CPT codes"},
    },
}

# Procedures this pack treats as needing prior authorization (illustrative, not a payer's real list).
PRIOR_AUTH_CPT = {"70553": "MRI brain with and without contrast", "72148": "MRI lumbar spine without contrast",
                  "29881": "Knee arthroscopy with meniscectomy", "93306": "Echocardiogram, complete",
                  "27447": "Total knee arthroplasty"}

# --- normalisation ---------------------------------------------------------------------

_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%y")
_ICD10 = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
_CPT = re.compile(r"^\d{5}$")


def parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip().replace("  ", " ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def normalize_name(value) -> str:
    """'SMITH, John A.' and 'John A Smith' compare equal."""
    if not value:
        return ""
    text = str(value).strip()
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first} {last}"
    return " ".join(sorted(re.sub(r"[^a-z ]", "", text.lower()).split()))


def normalize_id(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def valid_npi(value) -> bool:
    """NPIs are 10 digits whose last digit is a Luhn check digit over '80840' + the first 9."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 10:
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + digits[:9])):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(digits[9])


def valid_icd10(code) -> bool:
    return bool(_ICD10.match(str(code or "").strip().upper()))


def valid_cpt(code) -> bool:
    return bool(_CPT.match(str(code or "").strip()))


def normalize_value(spec_type: str, value):
    """The canonical form stored on the claim (ISO dates, float money)."""
    if spec_type == "date":
        parsed = parse_date(value)
        return parsed.isoformat() if parsed else value
    if spec_type == "money":
        parsed = parse_money(value)
        return parsed if parsed is not None else value
    return value
