"""X12 837P (Health Care Claim: Professional, 005010X222A1), simplified, one claim per file.

The provider side writes the claim with build_837; the mock payer reads it with parse_837,
the same way a clearinghouse hands claims to a payer. Loops written: submitter (1000A),
receiver (1000B), billing provider (2000A/2010AA), subscriber and payer (2000B/2010BA/BB),
claim (2300: CLM, REF*G1 prior authorization, REF*F8 original claim on a replacement, HI
diagnoses) and service lines (2400: LX, SV1, DTP*472). The billing provider address and tax
ID are synthetic placeholders.
"""

from datetime import date, datetime

from app.verticals.healthcare.x12_835 import X12Error

ICD_FIRST, ICD_OTHER = "ABK", "ABF"  # principal / other diagnosis qualifiers


def _amt(v: float) -> str:
    text = f"{v:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _d8(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%Y%m%d") if iso else ""


def build_837(claim: dict, payer_id: str, control: int, now: datetime) -> str:
    """claim: the payer-facing claim (see revenue.payer_claim)."""
    total = sum(l["charge"] for l in claim["lines"])
    isa = (f"ISA*00*{'':10}*00*{'':10}*ZZ*{'HEALTHOPS':<15}*ZZ*{payer_id:<15}*{now:%y%m%d}*{now:%H%M}*^*00501*"
           f"{control:09d}*0*P*:")
    gs = f"GS*HC*HEALTHOPS*{payer_id}*{now:%Y%m%d}*{now:%H%M}*{control}*X*005010X222A1"
    body = [f"ST*837*{control % 10000:04d}*005010X222A1",
            f"BHT*0019*00*{claim['claim_number']}*{now:%Y%m%d}*{now:%H%M}*CH",
            f"NM1*41*2*{claim['provider_name']}*****46*{claim['provider_npi']}", "PER*IC*BILLING OFFICE*TE*5555550100",
            f"NM1*40*2*{claim['payer_name']}*****46*{payer_id}",
            "HL*1**20*1", f"NM1*85*2*{claim['provider_name']}*****XX*{claim['provider_npi']}",
            "N3*100 MAIN STREET", "N4*SPRINGFIELD*IL*627010000", "REF*EI*990000001",
            "HL*2*1*22*0", "SBR*P*18*******CI",
            f"NM1*IL*1*{claim['patient_last']}*{claim['patient_first']}****MI*{claim['member_id']}"]
    if claim.get("patient_dob"):
        body.append(f"DMG*D8*{_d8(claim['patient_dob'])}*U")
    body += [f"NM1*PR*2*{claim['payer_name']}*****PI*{payer_id}",
             f"CLM*{claim['claim_number']}*{_amt(total)}***11:B:{claim.get('frequency', '1')}*Y*A*Y*Y"]
    if claim.get("prior_auth_number"):
        body.append(f"REF*G1*{claim['prior_auth_number']}")
    if claim.get("original_reference"):
        body.append(f"REF*F8*{claim['original_reference']}")
    codes = [c.replace(".", "") for c in claim.get("diagnosis_codes") or []]
    if codes:
        body.append("HI*" + "*".join(f"{ICD_FIRST if i == 0 else ICD_OTHER}:{c}" for i, c in enumerate(codes[:12])))
    for i, line in enumerate(claim["lines"], start=1):
        composite = ":".join(["HC", line["cpt"], *line.get("modifiers", [])])
        pointer = ":".join(str(n) for n in range(1, min(len(codes), 4) + 1)) or "1"
        body += [f"LX*{i}", f"SV1*{composite}*{_amt(line['charge'])}*UN*{line.get('units') or 1}***{pointer}",
                 f"DTP*472*D8*{_d8(claim['service_date'])}"]
    body.append(f"SE*{len(body) + 1}*{control % 10000:04d}")
    return "~\n".join([isa, gs, *body, f"GE*1*{control}", f"IEA*1*{control:09d}"]) + "~\n"


def parse_837(text: str) -> dict:
    """Read an 837P back into the payer-facing claim shape."""
    text = text.lstrip()
    if not text.startswith("ISA") or len(text) < 106:
        raise X12Error("Not an X12 file: it must start with an ISA segment")
    element, component, terminator = text[3], text[104], text[105]
    segments = [s.strip() for s in text.replace("\n", "").split(terminator) if s.strip()]
    claim: dict = {"lines": [], "diagnosis_codes": [], "prior_auth_number": None, "original_reference": None}
    count, inside, saw, line = 0, False, False, None
    for raw in segments:
        p = raw.split(element)
        tag = p[0]
        get = lambda i: p[i] if i < len(p) else ""  # noqa: E731
        if inside:
            count += 1
        if tag == "ST":
            if get(1) != "837":
                raise X12Error(f"Transaction set {get(1)} is not an 837")
            inside, count, saw = True, 1, True
        elif tag == "SE":
            if int(get(1)) != count:
                raise X12Error(f"SE says {get(1)} segments but the transaction has {count}")
            inside = False
        elif tag == "NM1" and get(1) == "85":
            claim["provider_name"], claim["provider_npi"] = get(3), get(9)
        elif tag == "NM1" and get(1) == "IL":
            claim["patient_last"], claim["patient_first"], claim["member_id"] = get(3), get(4), get(9)
        elif tag == "DMG":
            claim["patient_dob"] = datetime.strptime(get(2), "%Y%m%d").date().isoformat() if get(2) else None
        elif tag == "NM1" and get(1) == "PR":
            claim["payer_name"] = get(3)
        elif tag == "CLM":
            facility = get(5).split(component)
            claim["claim_number"], claim["frequency"] = get(1), (facility + ["", "", "1"])[2] or "1"
            claim["total_charge"] = float(get(2) or 0)
        elif tag == "REF" and get(1) == "G1":
            claim["prior_auth_number"] = get(2)
        elif tag == "REF" and get(1) == "F8":
            claim["original_reference"] = get(2)
        elif tag == "HI":
            for part in p[1:]:
                code = part.split(component)[1] if component in part else ""
                if code:
                    claim["diagnosis_codes"].append(code if len(code) <= 3 else f"{code[:3]}.{code[3:]}")
        elif tag == "SV1":
            composite = get(1).split(component)
            if len(composite) < 2:
                raise X12Error(f"Service line without a procedure code: {raw}")
            line = {"cpt": composite[1], "modifiers": [m for m in composite[2:] if m],
                    "charge": float(get(2) or 0), "units": int(float(get(4) or 1))}
            claim["lines"].append(line)
        elif tag == "DTP" and get(1) == "472" and line is not None:
            claim["service_date"] = datetime.strptime(get(3), "%Y%m%d").date().isoformat()
    if not saw:
        raise X12Error("No 837 transaction found")
    if not claim.get("claim_number") or not claim["lines"]:
        raise X12Error("837 has no claim or no service lines")
    if abs(sum(l["charge"] for l in claim["lines"]) - claim["total_charge"]) > 0.01:
        raise X12Error("CLM total doesn't equal the sum of the service lines")
    return claim
