"""X12 835 (Health Care Claim Payment/Advice, version 005010X221A1): write and read.

The mock payer writes 835 text with `build_835`, and the provider side reads every
remittance with `parse_835`, the same as it would read a file from a real clearinghouse.
Only the segments a billing team acts on are handled:

    ISA/GS/ST          envelope (separators are read from the ISA segment, not assumed)
    BPR, TRN, DTM*405  payment amount, method, trace number, production date
    N1*PR, N1*PE       payer and payee
    CLP                one claim: status, charge, paid, patient responsibility, payer control number
    CAS                adjustments (group + reason code + amount, up to six per segment)
    NM1*QC             patient
    SVC, DTM*472       one service line, its date
    AMT*B6, LQ*HE      allowed amount, remark codes
    SE/GE/IEA          trailers (the SE segment count is checked)

Everything else is skipped, not rejected, so a richer real-world file still parses.
"""

from datetime import date, datetime

from app.verticals.healthcare.carc import CLAIM_STATUS


class X12Error(ValueError):
    pass


# --- writing ------------------------------------------------------------------------------

def _amt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _d8(value: str | date) -> str:
    return (value if isinstance(value, date) else date.fromisoformat(value)).strftime("%Y%m%d")


def _cas(group: str, adjustments: list[dict]) -> list[str]:
    """CAS segments for one group; a segment holds at most six reason/amount pairs."""
    items = [a for a in adjustments if a["group"] == group]
    segments = []
    for start in range(0, len(items), 6):
        parts = ["CAS", group]
        for a in items[start:start + 6]:
            parts += [a["code"], _amt(a["amount"]), ""]
        segments.append("*".join(parts).rstrip("*"))
    return segments


def build_835(remit: dict, sender_id: str, receiver_id: str, control_number: int, now: datetime) -> str:
    """Write a remittance dict (the same shape parse_835 returns) as 835 text."""
    payment = remit["payment"]
    body: list[str] = [
        f"ST*835*{control_number % 10000:04d}",
        ("BPR*I*{amt}*C*ACH*CCP*01*011000015*DA*8800112233*1512345678**01*021000021*DA*4400998877*{d}"
         if payment["amount"] > 0 else "BPR*H*{amt}*C*NON************{d}").format(
            amt=_amt(payment["amount"]), d=_d8(payment["date"])),
        f"TRN*1*{payment['trace']}*1512345678",
        f"DTM*405*{_d8(payment['date'])}",
        f"N1*PR*{remit['payer']['name']}*XV*{remit['payer']['id']}",
        f"N1*PE*{remit['payee']['name']}*XX*{remit['payee']['npi']}",
    ]
    for index, claim in enumerate(remit["claims"], start=1):
        body.append(f"LX*{index}")
        body.append("*".join(["CLP", claim["claim_number"], claim["status_code"], _amt(claim["charge"]),
                              _amt(claim["paid"]), _amt(claim["patient_responsibility"]), "12",
                              claim["payer_control_number"], "11", claim.get("frequency", "1")]))
        for group in ("CO", "PR", "OA", "PI"):
            body += _cas(group, claim.get("adjustments", []))
        patient = claim["patient"]
        body.append(f"NM1*QC*1*{patient['last']}*{patient['first']}****MI*{patient['member_id']}")
        if claim.get("service_date"):
            body.append(f"DTM*232*{_d8(claim['service_date'])}")
        for line in claim["lines"]:
            code = ":".join(["HC", line["cpt"], *line.get("modifiers", [])])
            body.append(f"SVC*{code}*{_amt(line['charge'])}*{_amt(line['paid'])}**{line['units']}")
            if line.get("service_date"):
                body.append(f"DTM*472*{_d8(line['service_date'])}")
            for group in ("CO", "PR", "OA", "PI"):
                body += _cas(group, line.get("adjustments", []))
            if line.get("allowed") is not None:
                body.append(f"AMT*B6*{_amt(line['allowed'])}")
            body += [f"LQ*HE*{code}" for code in line.get("remarks", [])]
    body.append(f"SE*{len(body) + 1}*{control_number % 10000:04d}")

    isa = (f"ISA*00*{'':10}*00*{'':10}*ZZ*{sender_id:<15}*ZZ*{receiver_id:<15}*{now:%y%m%d}*{now:%H%M}*^*00501*"
           f"{control_number:09d}*0*P*:")
    segments = [isa, f"GS*HP*{sender_id}*{receiver_id}*{now:%Y%m%d}*{now:%H%M}*{control_number}*X*005010X221A1",
                *body, f"GE*1*{control_number}", f"IEA*1*{control_number:09d}"]
    return "~\n".join(segments) + "~\n"


# --- reading ------------------------------------------------------------------------------

def _num(value: str) -> float:
    try:
        return round(float(value), 2) if value else 0.0
    except ValueError as exc:
        raise X12Error(f"Not a number: {value!r}") from exc


def _date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat() if value else None
    except ValueError as exc:
        raise X12Error(f"Not a CCYYMMDD date: {value!r}") from exc


def _el(parts: list[str], index: int) -> str:
    return parts[index].strip() if index < len(parts) else ""


def _cas_items(parts: list[str]) -> list[dict]:
    group, out = _el(parts, 1), []
    for i in range(2, len(parts), 3):
        code = _el(parts, i)
        if code:
            out.append({"group": group, "code": code, "amount": _num(_el(parts, i + 1))})
    return out


def parse_835(text: str) -> dict:
    """Read 835 text into a remittance dict. Raises X12Error if it isn't a usable 835."""
    text = text.lstrip("﻿").lstrip()
    if not text.startswith("ISA") or len(text) < 106:
        raise X12Error("Not an X12 file: it must start with an ISA segment")
    element, component, terminator = text[3], text[104], text[105]
    segments = [s.strip().replace("\r", "").replace("\n", "") for s in text.split(terminator)]
    segments = [s for s in segments if s]

    remit: dict = {"control_number": None, "payer": {}, "payee": {}, "payment": {}, "claims": [], "warnings": []}
    claim: dict | None = None
    line: dict | None = None
    st_count = 0
    in_transaction = False
    saw_835 = False

    for raw in segments:
        parts = raw.split(element)
        tag = parts[0].strip()
        if in_transaction:
            st_count += 1
        if tag == "ISA":
            remit["control_number"] = _el(parts, 13)
        elif tag == "ST":
            if _el(parts, 1) != "835":
                raise X12Error(f"Transaction set {_el(parts, 1)} is not an 835")
            in_transaction, st_count, saw_835 = True, 1, True
        elif tag == "SE":
            if _el(parts, 1) and int(_el(parts, 1)) != st_count:
                raise X12Error(f"SE says {_el(parts, 1)} segments but the transaction has {st_count}")
            in_transaction = False
        elif tag == "BPR":
            remit["payment"] = {"handling": _el(parts, 1), "amount": _num(_el(parts, 2)),
                                "method": _el(parts, 4) or "NON", "date": _date(_el(parts, 16))}
        elif tag == "TRN":
            remit["payment"]["trace"] = _el(parts, 2)
        elif tag == "DTM" and _el(parts, 1) == "405":
            remit["payment"].setdefault("date", _date(_el(parts, 2)))
        elif tag == "N1" and _el(parts, 1) == "PR":
            remit["payer"] = {"name": _el(parts, 2), "id": _el(parts, 4)}
        elif tag == "N1" and _el(parts, 1) == "PE":
            remit["payee"] = {"name": _el(parts, 2), "npi": _el(parts, 4)}
        elif tag == "CLP":
            status = _el(parts, 2)
            claim = {"claim_number": _el(parts, 1), "status_code": status,
                     "status": CLAIM_STATUS.get(status, f"Status {status}"),
                     "charge": _num(_el(parts, 3)), "paid": _num(_el(parts, 4)),
                     "patient_responsibility": _num(_el(parts, 5)), "payer_control_number": _el(parts, 7),
                     "frequency": _el(parts, 9) or "1", "patient": {}, "service_date": None,
                     "adjustments": [], "lines": []}
            remit["claims"].append(claim)
            line = None
        elif tag == "CAS":
            if line is not None:
                line["adjustments"] += _cas_items(parts)
            elif claim is not None:
                claim["adjustments"] += _cas_items(parts)
        elif tag == "NM1" and _el(parts, 1) == "QC" and claim is not None:
            claim["patient"] = {"last": _el(parts, 3), "first": _el(parts, 4), "member_id": _el(parts, 9)}
        elif tag == "DTM" and _el(parts, 1) in ("232", "050") and claim is not None and line is None:
            claim["service_date"] = claim["service_date"] or _date(_el(parts, 2))
        elif tag == "SVC" and claim is not None:
            code = _el(parts, 1).split(component)
            if len(code) < 2:
                raise X12Error(f"Service line without a procedure code: {raw}")
            line = {"cpt": code[1], "modifiers": [m for m in code[2:] if m], "charge": _num(_el(parts, 2)),
                    "paid": _num(_el(parts, 3)), "units": int(_num(_el(parts, 5)) or 1), "service_date": None,
                    "allowed": None, "adjustments": [], "remarks": []}
            claim["lines"].append(line)
        elif tag == "DTM" and _el(parts, 1) == "472" and line is not None:
            line["service_date"] = _date(_el(parts, 2))
        elif tag == "AMT" and _el(parts, 1) == "B6" and line is not None:
            line["allowed"] = _num(_el(parts, 2))
        elif tag == "LQ" and _el(parts, 1) == "HE" and line is not None:
            line["remarks"].append(_el(parts, 2))

    if not saw_835:
        raise X12Error("No 835 transaction found")
    if in_transaction:
        raise X12Error("Transaction is missing its SE trailer")
    for c in remit["claims"]:
        balance = round(c["charge"] - c["paid"] - sum(a["amount"] for a in c["adjustments"])
                        - sum(a["amount"] for l in c["lines"] for a in l["adjustments"]), 2)
        if c["lines"] and abs(balance) > 0.01:
            remit["warnings"].append(f"Claim {c['claim_number']} doesn't balance: off by {balance:.2f}")
    return remit
