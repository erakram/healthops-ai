"""X12 276/277: claim status inquiry and response (005010X212), simplified.

When a payer hasn't answered a claim, the billing team asks for its status with a 276 and
the payer answers with a 277. The answer's STC segment carries a claim status category
code (what stage the claim is in) and a status code (why). Only the segments needed for
one claim per transaction are written and read; separators come from the ISA segment.
"""

from datetime import date, datetime

from app.verticals.healthcare.x12_835 import X12Error

# Claim status category codes (X12 external code list 507), the ones this pack uses.
CATEGORIES = {
    "A1": "Acknowledgement: receipt",
    "A2": "Acknowledgement: accepted into adjudication",
    "A7": "Acknowledgement: rejected for invalid information",
    "D0": "Data search unsuccessful: claim not found",
    "P1": "Pending: in process",
    "P3": "Pending: provider requested information",
    "F1": "Finalized: payment",
    "F2": "Finalized: denial",
}
# Claim status codes (code list 508), the ones this pack uses.
STATUS_CODES = {
    "20": "Accepted for processing",
    "35": "Claim/encounter not found",
    "65": "Claim/line has been paid",
    "88": "Entity's date of birth / member information is invalid",
    "294": "Supporting documentation (medical records) requested",
    "3": "Claim has been adjudicated and is awaiting payment cycle",
}


def _env(sender: str, receiver: str, control: int, now: datetime, kind: str) -> tuple[str, str]:
    isa = (f"ISA*00*{'':10}*00*{'':10}*ZZ*{sender:<15}*ZZ*{receiver:<15}*{now:%y%m%d}*{now:%H%M}*^*00501*"
           f"{control:09d}*0*P*:")
    gs = f"GS*{'HR' if kind == '276' else 'HN'}*{sender}*{receiver}*{now:%Y%m%d}*{now:%H%M}*{control}*X*005010X212"
    return isa, gs


def _wrap(isa: str, gs: str, body: list[str], control: int) -> str:
    body = [f"ST*{body[0]}*{control % 10000:04d}*005010X212", *body[1:]]
    body.append(f"SE*{len(body) + 1}*{control % 10000:04d}")
    return "~\n".join([isa, gs, *body, f"GE*1*{control}", f"IEA*1*{control:09d}"]) + "~\n"


def _d8(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%Y%m%d")


def build_276(claim: dict, sender: str, receiver: str, control: int, now: datetime) -> str:
    """claim: {claim_number, payer_name, payer_id, provider_name, provider_npi, patient_last, patient_first,
    member_id, charge, service_date, trace}"""
    isa, gs = _env(sender, receiver, control, now, "276")
    body = ["276", f"BHT*0010*13*{claim['trace']}*{now:%Y%m%d}",
            "HL*1**20*1", f"NM1*PR*2*{claim['payer_name']}*****PI*{claim['payer_id']}",
            "HL*2*1*21*1", f"NM1*41*2*{claim['provider_name']}*****46*{claim['provider_npi']}",
            "HL*3*2*19*1", f"NM1*1P*2*{claim['provider_name']}*****XX*{claim['provider_npi']}",
            "HL*4*3*22*0", "DMG*D8*19000101",
            f"NM1*IL*1*{claim['patient_last']}*{claim['patient_first']}****MI*{claim['member_id']}",
            f"TRN*1*{claim['trace']}", f"REF*EJ*{claim['claim_number']}", f"AMT*T3*{claim['charge']:.2f}",
            f"DTP*472*RD8*{_d8(claim['service_date'])}-{_d8(claim['service_date'])}"]
    return _wrap(isa, gs, body, control)


def build_277(inquiry: dict, status: dict, sender: str, receiver: str, control: int, now: datetime) -> str:
    """status: {category, code, effective (ISO date), message, paid_amount?, check_number?, payer_claim_number?}"""
    isa, gs = _env(sender, receiver, control, now, "277")
    stc = f"STC*{status['category']}:{status['code']}*{_d8(status['effective'])}**{inquiry['charge']:.2f}"
    if status.get("paid_amount") is not None:
        stc += f"*{status['paid_amount']:.2f}*{_d8(status.get('paid_on') or status['effective'])}**{_d8(status['effective'])}"
        stc += f"*{status.get('check_number') or ''}**"  # STC09 check number; STC10-11 unused
    else:
        stc += "*******"
    stc += f"*{status.get('message') or ''}"
    body = ["277", f"BHT*0010*08*{inquiry['trace']}*{now:%Y%m%d}*{now:%H%M}*DG",
            "HL*1**20*1", f"NM1*PR*2*{inquiry['payer_name']}*****PI*{inquiry['payer_id']}",
            "HL*2*1*21*1", f"NM1*41*2*{inquiry['provider_name']}*****46*{inquiry['provider_npi']}",
            "HL*3*2*19*1", f"NM1*1P*2*{inquiry['provider_name']}*****XX*{inquiry['provider_npi']}",
            "HL*4*3*22*0", f"NM1*IL*1*{inquiry['patient_last']}*{inquiry['patient_first']}****MI*{inquiry['member_id']}",
            f"TRN*2*{inquiry['trace']}", stc, f"REF*EJ*{inquiry['claim_number']}"]
    if status.get("payer_claim_number"):
        body.append(f"REF*1K*{status['payer_claim_number']}")
    body.append(f"DTP*472*RD8*{_d8(inquiry['service_date'])}-{_d8(inquiry['service_date'])}")
    return _wrap(isa, gs, body, control)


def parse_277(text: str) -> dict:
    text = text.lstrip()
    if not text.startswith("ISA") or len(text) < 106:
        raise X12Error("Not an X12 file: it must start with an ISA segment")
    element, component, terminator = text[3], text[104], text[105]
    segments = [s.strip() for s in text.replace("\n", "").split(terminator) if s.strip()]
    out: dict = {"claim_number": None, "trace": None, "statuses": [], "payer_claim_number": None}
    count, inside, saw = 0, False, False
    for raw in segments:
        parts = raw.split(element)
        tag = parts[0]
        if inside:
            count += 1
        if tag == "ST":
            if parts[1] != "277":
                raise X12Error(f"Transaction set {parts[1]} is not a 277")
            inside, count, saw = True, 1, True
        elif tag == "SE":
            if int(parts[1]) != count:
                raise X12Error(f"SE says {parts[1]} segments but the transaction has {count}")
            inside = False
        elif tag == "TRN":
            out["trace"] = parts[2]
        elif tag == "REF" and parts[1] == "EJ":
            out["claim_number"] = parts[2]
        elif tag == "REF" and parts[1] == "1K":
            out["payer_claim_number"] = parts[2]
        elif tag == "STC":
            category, code = (parts[1].split(component) + [""])[:2]
            get = lambda i: parts[i] if i < len(parts) else ""  # noqa: E731
            paid = get(5)
            out["statuses"].append({
                "category": category, "code": code,
                "category_text": CATEGORIES.get(category, f"Category {category}"),
                "code_text": STATUS_CODES.get(code, f"Status {code}"),
                "effective": datetime.strptime(get(2), "%Y%m%d").date().isoformat() if get(2) else None,
                "charge": float(get(4)) if get(4) else None,
                "paid_amount": float(paid) if paid else None,
                "check_number": get(9) or None,
                "message": get(12) or None,
            })
    if not saw:
        raise X12Error("No 277 transaction found")
    if not out["statuses"]:
        raise X12Error("277 has no STC status segment")
    return out
