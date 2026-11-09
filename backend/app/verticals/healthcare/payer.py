"""A mock payer: adjudicates claims, writes 835 remittances and decides appeals.

It stands in for an insurer's claim system, so the provider side has something real to
react to. Its decisions are deterministic: the same claim always gets the same answer,
which is what lets the denial agents be scored in evals. It knows things the provider
doesn't (its eligibility file, which authorizations it has on record, member
deductibles), loaded from sample_data/payer_state.json, which the sample generator
writes alongside the synthetic cases.

Adjudication order, as real payers do it: claim-level edits (timely filing,
eligibility) first, then line edits (authorization, records review, bundling), then
pricing (fee schedule, deductible).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from app.config import BACKEND_ROOT
from app.verticals.healthcare.policies import bundled_with, criterion_satisfied
from app.verticals.healthcare.schema import PRIOR_AUTH_CPT, normalize_id, parse_date

STATE_FILE = BACKEND_ROOT / "sample_data" / "payer_state.json"


@dataclass(frozen=True)
class PayerProfile:
    name: str
    id: str
    allowed_pct: float  # contracted rate as a share of billed charges
    timely_filing_days: int
    appeal_window_days: int


PROFILES = {p.name: p for p in [
    PayerProfile("Northwind Health Plan", "NWH01", 0.62, 90, 180),
    PayerProfile("Bluewater Mutual", "BWM02", 0.58, 180, 120),
    PayerProfile("Cedar Ridge Health", "CRH03", 0.66, 90, 90),
    PayerProfile("Summit CareShield", "SCS04", 0.60, 120, 180),
]}

# Services this payer only pays after reviewing the medical record (policy MP-2041).
RECORDS_REQUIRED = {"64483", "64484", "62323"}


def profile_for(payer_name: str | None) -> PayerProfile:
    if payer_name in PROFILES:
        return PROFILES[payer_name]
    return PayerProfile(payer_name or "Unknown payer", "PAYER0", 0.60, 90, 180)


@lru_cache
def payer_state() -> dict:
    """What the payer knows that the provider doesn't. Empty when no samples were generated."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"auth_not_on_file": [], "terminations": {}, "deductible_remaining": {}}


def _control_number(profile: PayerProfile, claim_number: str, suffix: str) -> str:
    digest = hashlib.sha1(f"{claim_number}:{suffix}".encode()).hexdigest()[:10].upper()
    return f"{profile.id[:3]}{digest}"


def _deny(line: dict, code: str, remarks: list[str]) -> dict:
    return {**line, "paid": 0.0, "allowed": 0.0, "remarks": remarks,
            "adjustments": [{"group": "CO", "code": code, "amount": line["charge"]}]}


def adjudicate(claim: dict, submitted_on: date) -> dict:
    """Decide one submitted claim. `claim` is the payer-facing claim (see pipeline.payer_claim).
    Returns one CLP claim dict in the shape parse_835 produces."""
    profile = profile_for(claim["payer_name"])
    state = payer_state()
    dos = date.fromisoformat(claim["service_date"])
    member = normalize_id(claim["member_id"])
    lines = [{"cpt": l["cpt"], "modifiers": list(l.get("modifiers") or []), "charge": round(l["charge"], 2),
              "units": l.get("units") or 1, "service_date": claim["service_date"]} for l in claim["lines"]]
    cpts = [l["cpt"] for l in lines]

    claim_level: tuple[str, list[str]] | None = None
    if (submitted_on - dos).days > profile.timely_filing_days:
        claim_level = ("29", [])
    term = state["terminations"].get(member)
    if not claim_level and term and date.fromisoformat(term) < dos:
        claim_level = ("27", ["N30"])

    auth = normalize_id(claim.get("prior_auth_number"))
    deductible = float(state["deductible_remaining"].get(member, 0.0))
    out_lines = []
    for line in lines:
        if claim_level:
            out_lines.append(_deny(line, *claim_level))
            continue
        cpt = line["cpt"]
        if cpt in PRIOR_AUTH_CPT and (not auth or auth in state["auth_not_on_file"]):
            out_lines.append(_deny(line, "197", ["M62"]))
            continue
        if cpt in RECORDS_REQUIRED and claim.get("frequency", "1") == "1":
            out_lines.append(_deny(line, "50", ["M127", "N115"]))
            continue
        if bundled_with(cpt, cpts) and "25" not in line["modifiers"]:
            out_lines.append(_deny(line, "97", ["M15"]))
            continue
        allowed = round(line["charge"] * profile.allowed_pct, 2)
        adjustments = [{"group": "CO", "code": "45", "amount": round(line["charge"] - allowed, 2)}]
        patient = round(min(deductible, allowed), 2)
        if patient > 0:
            adjustments.append({"group": "PR", "code": "1", "amount": patient})
            deductible -= patient
        out_lines.append({**line, "allowed": allowed, "paid": round(allowed - patient, 2),
                          "adjustments": adjustments, "remarks": []})

    paid = round(sum(l["paid"] for l in out_lines), 2)
    return {
        "claim_number": claim["claim_number"],
        "status_code": "1" if any(l["allowed"] > 0 for l in out_lines) else "4",
        "charge": round(sum(l["charge"] for l in out_lines), 2), "paid": paid,
        "patient_responsibility": round(sum(a["amount"] for l in out_lines for a in l["adjustments"]
                                            if a["group"] == "PR"), 2),
        "payer_control_number": _control_number(profile, claim["claim_number"], claim.get("frequency", "1")),
        "frequency": claim.get("frequency", "1"),
        "patient": {"last": claim["patient_last"], "first": claim["patient_first"], "member_id": claim["member_id"]},
        "service_date": claim["service_date"], "adjustments": [], "lines": out_lines,
    }


def reversal_of(previous: dict) -> dict:
    """CLP02=22: takes back a previous payment before a replacement (corrected) claim is paid."""
    def neg(items):
        return [{**a, "amount": -a["amount"]} for a in items]
    return {**previous, "status_code": "22", "charge": -previous["charge"], "paid": -previous["paid"],
            "patient_responsibility": -previous["patient_responsibility"], "adjustments": neg(previous["adjustments"]),
            "lines": [{**l, "charge": -l["charge"], "paid": -l["paid"],
                       "allowed": -(l.get("allowed") or 0), "adjustments": neg(l["adjustments"])}
                      for l in previous["lines"]]}


def remittance(payer_name: str, payee: dict, claims: list[dict], paid_on: date, trace: str) -> dict:
    profile = profile_for(payer_name)
    return {"payer": {"name": profile.name, "id": profile.id}, "payee": payee,
            "payment": {"amount": round(sum(c["paid"] for c in claims), 2), "date": paid_on.isoformat(),
                        "trace": trace},
            "claims": claims}


# --- appeals -------------------------------------------------------------------------------

@dataclass
class AppealDecision:
    overturned: bool
    reason: str
    satisfied: list[str]  # criterion ids the appeal proved
    missing: list[str]  # criterion ids it didn't


def review_appeal(category: str, denied_cpts: list[str], citations: list[dict], record: dict,
                  service_date: str, policy: dict | None) -> AppealDecision:
    """The payer's appeal reviewer. Only citations the provider side verified against the
    attached records count; what the letter merely asserts doesn't."""
    verified = [c for c in citations if c.get("verified")]

    if category == "authorization":
        auth = (record.get("prior_authorization") or {})
        number = normalize_id((auth.get("auth_number") or {}).get("value"))
        cited = number and any(c["source"] == "prior_authorization" and number in normalize_id(c["quote"])
                               for c in verified)
        dos = parse_date(service_date)
        start = parse_date((auth.get("valid_from") or {}).get("value"))
        end = parse_date((auth.get("valid_to") or {}).get("value"))
        approved = {c["code"] for c in (auth.get("approved_cpt_codes") or {}).get("value", [])}
        in_window = bool(dos and start and end and start <= dos <= end)
        covers = all(cpt in approved for cpt in denied_cpts)
        if cited and in_window and covers:
            return AppealDecision(True, f"Authorization {number} was issued for this service and covers the date of "
                                        "service. Denial reversed; claim reprocessed for payment.", ["A1", "A2"], [])
        missing = ([] if cited else ["A1"]) + ([] if in_window and covers else ["A2"])
        why = ("the appeal doesn't cite an authorization number from the determination letter" if not cited
               else "the authorization doesn't cover this procedure on the date of service")
        return AppealDecision(False, f"Denial upheld: {why}.", [m for m in ("A1", "A2") if m not in missing], missing)

    if category == "medical_necessity" and policy:
        notes = [c["quote"] for c in verified if c["source"].startswith("clinical_note")]
        satisfied = [c["id"] for c in policy["criteria"] if any(criterion_satisfied(c, q) for q in notes)]
        missing = [c["id"] for c in policy["criteria"] if c["id"] not in satisfied]
        if not missing:
            return AppealDecision(True, f"The medical record meets every criterion of {policy['id']} "
                                        f"({policy['title']}). Denial reversed.", satisfied, [])
        texts = "; ".join(f"{c['id']}: {c['text']}" for c in policy["criteria"] if c["id"] in missing)
        return AppealDecision(False, f"Denial upheld: the record cited doesn't show {texts}", satisfied, missing)

    if category == "coding_bundling":
        return AppealDecision(False, "Denial upheld: a bundled E/M is paid only on a corrected claim with modifier 25, "
                                     "not on appeal.", [], ["C1"])
    if category == "timely_filing":
        return AppealDecision(False, "Denial upheld: no proof that the claim was first received within the filing "
                                     "limit.", [], ["T1"])
    if category == "eligibility":
        return AppealDecision(False, "Denial upheld: the member's coverage had ended before the date of service.", [], [])
    return AppealDecision(False, "Denial upheld: this adjustment isn't appealable.", [], [])


def appeal_payment(original: dict, overturned_cpts: list[str], payer_name: str) -> dict:
    """The CLP the payer sends after overturning: pays the lines it had denied."""
    profile = profile_for(payer_name)
    lines = []
    for line in original["lines"]:
        if line["cpt"] not in overturned_cpts or line["paid"] > 0:
            continue
        allowed = round(line["charge"] * profile.allowed_pct, 2)
        lines.append({**line, "allowed": allowed, "paid": allowed, "remarks": [],
                      "adjustments": [{"group": "CO", "code": "45", "amount": round(line["charge"] - allowed, 2)}]})
    return {**original, "status_code": "1", "charge": round(sum(l["charge"] for l in lines), 2),
            "paid": round(sum(l["paid"] for l in lines), 2), "patient_responsibility": 0.0,
            "payer_control_number": original["payer_control_number"] + "A", "adjustments": [], "lines": lines}


# --- prior authorization ------------------------------------------------------------------

def eligibility(payer_name: str | None, member_id: str | None, on: date) -> dict:
    """The payer's eligibility answer (what a 270/271 check returns), from its own records."""
    term = payer_state()["terminations"].get(normalize_id(member_id))
    if term and date.fromisoformat(term) < on:
        return {"active": False, "terminated_on": term, "payer": profile_for(payer_name).name}
    return {"active": True, "terminated_on": None, "payer": profile_for(payer_name).name}


def requires_authorization(cpt: str) -> bool:
    return cpt in PRIOR_AUTH_CPT


def medical_policy_for(cpt: str) -> dict | None:
    from app.verticals.healthcare.policies import all_policies
    return next((p for p in all_policies().values() if p["kind"] == "medical" and cpt in p["cpt_codes"]), None)


def _in_records(quote: str, documents: list[dict]) -> bool:
    import re
    clean = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())  # noqa: E731
    q = clean(quote)
    return len(q) >= 12 and any(q in clean(d["text"]) for d in documents)


def review_authorization(request: dict, payer_name: str, attempt: int, today: date) -> dict:
    """The payer's utilization review of a PAS request (see fhir_pas.read_request).

    Quotes count only if they appear in the attached clinical records (the payer checks
    them itself; it doesn't trust the provider's word). All policy criteria shown ->
    approved. Something missing -> pended with a request for that information on the first
    review, denied on a later one."""
    elig = eligibility(payer_name, request["member_id"], date.fromisoformat(request["scheduled_date"]) if
                       request.get("scheduled_date") else today)
    if not elig["active"]:
        return {"action": "A3", "disposition": f"Denied: coverage ended {elig['terminated_on']}.", "notes": [],
                "satisfied": [], "missing": []}
    needed = [c for c in request["cpts"] if requires_authorization(c)]
    if not needed:
        return {"action": "A1", "disposition": "No authorization required for these services.", "notes": [],
                "satisfied": [], "missing": []}

    clinical = [d for d in request["documents"] if "note" in (d["title"] or "").lower()]
    shown = [q["quote"] for q in request["quotes"] if _in_records(q["quote"], clinical)]
    satisfied, missing = [], []
    for cpt in needed:
        policy = medical_policy_for(cpt)
        for c in (policy or {}).get("criteria", []):
            (satisfied if any(criterion_satisfied(c, q) for q in shown) else missing).append(
                {"policy": policy["id"], "id": c["id"], "text": c["text"]})
    if not missing:
        digest = hashlib.sha1(f"{request['order_number']}:{payer_name}".encode()).hexdigest()
        scheduled = date.fromisoformat(request["scheduled_date"]) if request.get("scheduled_date") else today
        from datetime import timedelta
        return {"action": "A1", "auth_number": f"PA-{int(digest[:8], 16) % 9_000_000 + 1_000_000}",
                "start": min(today, scheduled).isoformat(), "end": (max(today, scheduled) + timedelta(days=60)).isoformat(),
                "disposition": "Approved: medical necessity criteria met.", "notes": [],
                "satisfied": [c["id"] for c in satisfied], "missing": []}
    asks = [f"{c['policy']} {c['id']}: {c['text']}" for c in missing]
    if attempt <= 1:
        return {"action": "A4", "disposition": "Pended: additional clinical information is needed.",
                "notes": [f"Please send documentation showing {a}" for a in asks],
                "satisfied": [c["id"] for c in satisfied], "missing": [c["id"] for c in missing]}
    return {"action": "A3", "disposition": "Denied: medical necessity not established. Missing: " + "; ".join(asks),
            "notes": ["The ordering physician may request a peer-to-peer review."],
            "satisfied": [c["id"] for c in satisfied], "missing": [c["id"] for c in missing]}
