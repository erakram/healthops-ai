"""Deterministic claim checks.

Anything that can be decided by comparing values is a rule, not an LLM call: rules are
free, instant, explainable and always give the same answer. The LLM reviewer only
looks for what rules can't (e.g. a clinical note that doesn't support a billed service).

Input is the claim's extracted record: {document_type: {field: {value, confidence, evidence}}}.
"""

from dataclasses import dataclass, field

from app.verticals.healthcare.schema import (
    DOCUMENT_TYPES, PRIOR_AUTH_CPT, normalize_id, normalize_name, parse_date, parse_money,
    valid_cpt, valid_icd10, valid_npi,
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class RuleFinding:
    code: str
    severity: str
    title: str
    detail: str
    evidence: list[dict] = field(default_factory=list)
    source: str = "rule"


def _field(record: dict, doc: str, name: str):
    entry = (record.get(doc) or {}).get(name) or {}
    return entry.get("value")


def _evidence(record: dict, *refs: tuple[str, str]) -> list[dict]:
    out = []
    for doc, name in refs:
        entry = (record.get(doc) or {}).get(name) or {}
        if entry.get("evidence"):
            out.append({**entry["evidence"], "label": f"{DOCUMENT_TYPES[doc]['label']}: {name.replace('_', ' ')}"})
    return out


def _line_items(record: dict, doc: str) -> list[dict]:
    return _field(record, doc, "line_items") or []


def run_rules(record: dict, existing_claims: list[dict] | None = None) -> list[RuleFinding]:
    """existing_claims: other claims' {number, member_id, service_date, cpt_codes} for duplicate checks."""
    findings: list[RuleFinding] = []
    add = findings.append
    claim, invoice, card = record.get("claim_form"), record.get("invoice"), record.get("insurance_card")

    # --- documents present --------------------------------------------------------
    if not claim:
        add(RuleFinding("DOC-MISSING-CLAIM", "critical", "No claim form found",
                        "None of the uploaded documents was recognised as a claim form."))
        return findings
    if not invoice:
        add(RuleFinding("DOC-MISSING-INVOICE", "high", "Itemized invoice is missing",
                        "Payers require an itemized invoice to support the billed charges."))
    if not card:
        add(RuleFinding("DOC-MISSING-CARD", "high", "Insurance card is missing",
                        "Eligibility can't be checked without the member's insurance card."))

    # --- required fields ----------------------------------------------------------
    for doc_type, fields in record.items():
        if doc_type not in DOCUMENT_TYPES:
            continue
        for spec in DOCUMENT_TYPES[doc_type]["fields"]:
            if spec.required and not _field(record, doc_type, spec.name):
                add(RuleFinding(f"FIELD-MISSING", "medium",
                                f"{spec.label} is missing on the {DOCUMENT_TYPES[doc_type]['label'].lower()}",
                                f"The agent couldn't find '{spec.label}' in the document."))

    # --- identity -----------------------------------------------------------------
    claim_name = normalize_name(_field(record, "claim_form", "patient_name"))
    for doc_type, name_field in (("invoice", "patient_name"), ("insurance_card", "member_name"),
                                 ("clinical_note", "patient_name")):
        other = normalize_name(_field(record, doc_type, name_field))
        if claim_name and other and other != claim_name:
            add(RuleFinding("NAME-MISMATCH", "high", f"Patient name differs on the {DOCUMENT_TYPES[doc_type]['label'].lower()}",
                            f"Claim form says '{_field(record, 'claim_form', 'patient_name')}', "
                            f"{DOCUMENT_TYPES[doc_type]['label'].lower()} says '{_field(record, doc_type, name_field)}'.",
                            _evidence(record, ("claim_form", "patient_name"), (doc_type, name_field))))

    claim_dob = parse_date(_field(record, "claim_form", "patient_dob"))
    note_dob = parse_date(_field(record, "clinical_note", "patient_dob"))
    if claim_dob and note_dob and claim_dob != note_dob:
        add(RuleFinding("DOB-MISMATCH", "high", "Date of birth differs between claim and clinical note",
                        f"Claim form: {claim_dob.isoformat()}, clinical note: {note_dob.isoformat()}.",
                        _evidence(record, ("claim_form", "patient_dob"), ("clinical_note", "patient_dob"))))

    claim_member = normalize_id(_field(record, "claim_form", "member_id"))
    card_member = normalize_id(_field(record, "insurance_card", "member_id"))
    if claim_member and card_member and claim_member != card_member:
        add(RuleFinding("MEMBER-ID-MISMATCH", "critical", "Member ID doesn't match the insurance card",
                        f"Claim form: {_field(record, 'claim_form', 'member_id')}, "
                        f"card: {_field(record, 'insurance_card', 'member_id')}. The payer will reject this claim.",
                        _evidence(record, ("claim_form", "member_id"), ("insurance_card", "member_id"))))

    npi = _field(record, "claim_form", "provider_npi")
    if npi and not valid_npi(npi):
        add(RuleFinding("NPI-INVALID", "high", "Provider NPI fails the check-digit test",
                        f"'{npi}' is not a valid 10-digit NPI. It was probably mistyped.",
                        _evidence(record, ("claim_form", "provider_npi"))))

    # --- coverage -----------------------------------------------------------------
    dos = parse_date(_field(record, "claim_form", "service_date"))
    start = parse_date(_field(record, "insurance_card", "coverage_start"))
    end = parse_date(_field(record, "insurance_card", "coverage_end"))
    if dos and ((start and dos < start) or (end and dos > end)):
        add(RuleFinding("COVERAGE-INACTIVE", "critical", "Service date is outside the coverage period",
                        f"Service on {dos.isoformat()}, coverage {start.isoformat() if start else '?'} to "
                        f"{end.isoformat() if end else 'open'}.",
                        _evidence(record, ("claim_form", "service_date"), ("insurance_card", "coverage_start"),
                                  ("insurance_card", "coverage_end"))))

    # --- amounts ------------------------------------------------------------------
    claim_total = parse_money(_field(record, "claim_form", "total_charge"))
    invoice_total = parse_money(_field(record, "invoice", "total_amount"))
    if claim_total is not None and invoice_total is not None and abs(claim_total - invoice_total) > 0.01:
        add(RuleFinding("AMOUNT-MISMATCH", "high", "Claim total doesn't match the invoice",
                        f"Claim form: ${claim_total:,.2f}, invoice: ${invoice_total:,.2f} "
                        f"(difference ${abs(claim_total - invoice_total):,.2f}).",
                        _evidence(record, ("claim_form", "total_charge"), ("invoice", "total_amount"))))

    claim_lines = _line_items(record, "claim_form")
    line_sum = sum(parse_money(item.get("charge")) or 0 for item in claim_lines)
    if claim_lines and claim_total is not None and abs(line_sum - claim_total) > 0.01:
        add(RuleFinding("LINE-SUM-MISMATCH", "medium", "Line items don't add up to the claim total",
                        f"Lines sum to ${line_sum:,.2f}, the claim total is ${claim_total:,.2f}.",
                        _evidence(record, ("claim_form", "total_charge"))))

    # --- codes --------------------------------------------------------------------
    for item in claim_lines:
        if item.get("cpt") and not valid_cpt(item["cpt"]):
            add(RuleFinding("CPT-INVALID", "medium", f"'{item['cpt']}' is not a valid CPT code format",
                            "CPT codes are five digits.", [item["evidence"]] if item.get("evidence") else []))
    for dx in _field(record, "claim_form", "diagnosis_codes") or []:
        code = dx.get("code") if isinstance(dx, dict) else dx
        if code and not valid_icd10(code):
            add(RuleFinding("ICD-INVALID", "medium", f"'{code}' is not a valid ICD-10 code format",
                            "ICD-10-CM codes look like a letter, two characters, then an optional dot and up to four more.",
                            [dx["evidence"]] if isinstance(dx, dict) and dx.get("evidence") else []))

    # --- prior authorization ------------------------------------------------------
    auth = record.get("prior_authorization")
    approved = {str((c.get("code") if isinstance(c, dict) else c) or "").strip()
                for c in (_field(record, "prior_authorization", "approved_cpt_codes") or [])}
    for item in claim_lines:
        cpt = str(item.get("cpt") or "").strip()
        if cpt not in PRIOR_AUTH_CPT:
            continue
        if not auth:
            add(RuleFinding("PRIOR-AUTH-MISSING", "high", f"CPT {cpt} needs prior authorization",
                            f"{PRIOR_AUTH_CPT[cpt]} requires prior authorization and none was attached.",
                            [item["evidence"]] if item.get("evidence") else []))
        elif cpt not in approved:
            add(RuleFinding("PRIOR-AUTH-MISMATCH", "high", f"CPT {cpt} isn't covered by the authorization",
                            f"The authorization approves {', '.join(sorted(approved)) or 'no codes'}.",
                            [item["evidence"]] if item.get("evidence") else []))
        else:
            valid_from = parse_date(_field(record, "prior_authorization", "valid_from"))
            valid_to = parse_date(_field(record, "prior_authorization", "valid_to"))
            if dos and ((valid_from and dos < valid_from) or (valid_to and dos > valid_to)):
                add(RuleFinding("PRIOR-AUTH-EXPIRED", "high", "Service date is outside the authorization window",
                                f"Authorized {valid_from} to {valid_to}, service on {dos}.",
                                _evidence(record, ("prior_authorization", "valid_from"),
                                          ("prior_authorization", "valid_to"))))

    # --- duplicates ---------------------------------------------------------------
    cpts = {str(i.get("cpt")) for i in claim_lines if i.get("cpt")}
    for other in existing_claims or []:
        if (claim_member and normalize_id(other.get("member_id")) == claim_member and dos
                and other.get("service_date") == dos.isoformat() and cpts & set(other.get("cpt_codes") or [])):
            add(RuleFinding("DUPLICATE-CLAIM", "high", f"Possible duplicate of claim {other['number']}",
                            "Same member, date of service and procedure codes as an existing claim."))
            break

    findings.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return findings


def risk_level(findings) -> str:
    severities = [f.severity for f in findings]
    if "critical" in severities or severities.count("high") >= 2:
        return "high"
    if "high" in severities or "medium" in severities:
        return "medium"
    return "low"
