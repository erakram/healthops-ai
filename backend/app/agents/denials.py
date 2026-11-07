"""Denial Triage Agent: what went wrong, can the money be recovered, and by when.

Most of triage is rules, because most of it is lookup and arithmetic: the reason code
gives the category, the payer's contract gives the deadlines, the packet shows whether
an authorization existed. The LLM is used for the one judgement rules can't make well:
whether the clinical documentation meets a policy's criteria. Even then, a criterion
only counts as met if the model quotes the sentence that shows it, and that quote is
found in the note.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.core.llm import LLM, LLMResult
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare.carc import CARCS, RARCS, category_of
from app.verticals.healthcare.payer import profile_for
from app.verticals.healthcare.policies import bundled_with, get_policy, search_policies
from app.verticals.healthcare.prompts import ASSESS_SYSTEM
from app.verticals.healthcare.schema import normalize_id, parse_date

ACTIONS = {
    "appeal": "Appeal",
    "corrected_claim": "Send a corrected claim",
    "bill_patient": "Bill the patient",
    "write_off": "Write off",
    "manual_review": "Needs a person to review",
}

# Rough recovery odds by action, used only to rank the worklist.
WIN_PROBABILITY = {"appeal": 0.85, "corrected_claim": 0.9, "bill_patient": 0.55, "write_off": 0.0,
                   "manual_review": 0.3}


@dataclass
class TriageResult:
    category: str
    action: str
    reasons: list[str]
    deadline: str | None
    win_probability: float
    expected_recovery: float
    policy_id: str | None = None
    retrieval: list[dict] = field(default_factory=list)
    criteria: list[dict] = field(default_factory=list)
    summary: str = ""
    fix: dict | None = None  # for corrected claims: what to change
    llm: LLMResult | None = None

    def as_json(self) -> dict:
        return {"category": self.category, "action": self.action, "reasons": self.reasons,
                "deadline": self.deadline, "policy_id": self.policy_id, "retrieval": self.retrieval,
                "criteria": self.criteria, "summary": self.summary, "fix": self.fix}


def _value(record: dict, doc: str, name: str):
    return ((record.get(doc) or {}).get(name) or {}).get("value")


def retrieval_query(denial: dict, record: dict) -> str:
    carc = CARCS.get(denial["code"])
    lines = (record.get("claim_form") or {}).get("line_items", {}).get("value", [])
    descriptions = {l["cpt"]: l.get("description") or "" for l in lines}
    parts = [f"reason {denial['code']}", carc.description if carc else "",
             *(RARCS.get(r, "") for r in denial.get("remarks", [])),
             *(f"{l['cpt']} {descriptions.get(l['cpt'], '')}" for l in denial["lines"])]
    return " ".join(p for p in parts if p)


def choose_policy(denial: dict, record: dict) -> tuple[dict | None, list[dict]]:
    """BM25 over the policy library, preferring a hit that governs this reason code."""
    hits = search_policies(retrieval_query(denial, record), k=4)
    retrieved = [{"id": h.id, "title": get_policy(h.id)["title"], "score": h.score, "matched": h.matched[:8]}
                 for h in hits]
    for hit in hits:
        policy = get_policy(hit.id)
        if denial["code"] in policy["carc_codes"]:
            return policy, retrieved
    return (get_policy(hits[0].id) if hits else None), retrieved


def assess_criteria(llm: LLM, policy: dict, criteria: list[dict], denial: dict, note: tuple[int, ParsedPdf],
                    model: str | None = None) -> tuple[list[dict], str, LLMResult]:
    note_id, pdf = note
    payload = {
        "denial": {"reason": f"{denial['group']}-{denial['code']}: {CARCS[denial['code']].description}"
                   if denial["code"] in CARCS else denial["code"],
                   "services": [l["cpt"] for l in denial["lines"]]},
        "policy": {"id": policy["id"], "title": policy["title"],
                   "criteria": [{"id": c["id"], "text": c["text"]} for c in criteria]},
        "documentation": pdf.text[:12_000],
    }
    result = llm.complete_json(ASSESS_SYSTEM, json.dumps(payload, indent=1), model=model)
    answers = {str(a.get("id")): a for a in result.data.get("criteria") or [] if isinstance(a, dict)}
    out = []
    for c in criteria:
        answer = answers.get(c["id"], {})
        quote = str(answer.get("quote") or "")
        hit = locate(pdf, quote) if quote else None
        said_met = answer.get("met") is True
        out.append({"id": c["id"], "text": c["text"], "met": said_met and hit is not None, "model_said_met": said_met,
                    "quote": hit["text"] if hit else quote, "explanation": str(answer.get("explanation") or ""),
                    "evidence": {"document_id": note_id, **hit, "label": "Clinical note"} if hit else None})
    return out, str(result.data.get("summary") or ""), result


def triage_denial(denial: dict, record: dict, docs: dict[str, tuple[int, ParsedPdf]], context: dict,
                  llm: LLM | None, model: str | None = None) -> TriageResult:
    """denial: {group, code, remarks, lines: [{cpt, modifiers, charge, amount}], amount}
    context: {payer_name, service_date, remit_date, first_submitted_on, claim_cpts}"""
    profile = profile_for(context["payer_name"])
    remit_date = date.fromisoformat(context["remit_date"])
    dos = date.fromisoformat(context["service_date"])
    category = "patient_responsibility" if denial["group"] == "PR" else category_of(denial["code"])
    policy, retrieved = choose_policy(denial, record)
    charges = sum(l["charge"] for l in denial["lines"])
    expected_allowed = round(charges * profile.allowed_pct, 2)
    appeal_by = (remit_date + timedelta(days=profile.appeal_window_days)).isoformat()
    reasons: list[str] = []
    criteria: list[dict] = []
    summary, fix, result = "", None, None
    action, deadline = "manual_review", appeal_by

    if category == "patient_responsibility":
        action, deadline = "bill_patient", (remit_date + timedelta(days=30)).isoformat()
        reasons.append(f"{denial['group']}-{denial['code']} is the member's share under their plan "
                       f"({CARCS[denial['code']].description.lower() if denial['code'] in CARCS else 'cost sharing'}), "
                       "not a payer error.")
        reasons.append("Send the patient a statement within 30 days of the remittance.")

    elif category == "authorization":
        number = _value(record, "prior_authorization", "auth_number")
        start = parse_date(_value(record, "prior_authorization", "valid_from"))
        end = parse_date(_value(record, "prior_authorization", "valid_to"))
        approved = {c["code"] for c in (record.get("prior_authorization") or {}).get("approved_cpt_codes", {})
                    .get("value", [])}
        denied_cpts = [l["cpt"] for l in denial["lines"]]
        if number and start and end and start <= dos <= end and all(c in approved for c in denied_cpts):
            action = "appeal"
            reasons.append(f"The packet has authorization {number}, valid {start:%m/%d/%Y} to {end:%m/%d/%Y}, "
                           f"approving {', '.join(denied_cpts)}: the service was authorized.")
            reasons.append("The payer most likely didn't link the authorization to the claim. Appeal with the "
                           "determination letter attached.")
            criteria = [{"id": "A1", "text": "Approved authorization number on file", "met": True, "quote": number},
                        {"id": "A2", "text": "Validity covers the date of service and the billed procedure",
                         "met": True, "quote": f"{start:%m/%d/%Y} to {end:%m/%d/%Y}"}]
        else:
            action, deadline = "write_off", None
            reasons.append("No authorization in the packet covers this service on its date of service.")
            reasons.append("Ask the payer for a retro-authorization if its policy allows one; otherwise write it off.")

    elif category in ("medical_necessity", "coding_bundling"):
        note = docs.get("clinical_note")
        if category == "coding_bundling":
            partner = next((bundled_with(l["cpt"], context["claim_cpts"]) for l in denial["lines"]), None)
            if partner:
                reasons.append(f"CPT {denial['lines'][0]['cpt']} was bundled into CPT {partner} billed the same day "
                               "(national correct coding pair).")
        if not policy or not policy["criteria"]:
            reasons.append("No policy with checkable criteria was found for this denial.")
        elif note is None:
            reasons.append("There's no clinical note in the packet to check the policy against.")
        elif llm is None:
            reasons.append("No model is configured to check the documentation against the policy.")
        else:
            criteria, summary, result = assess_criteria(llm, policy, policy["criteria"], denial, note, model)
            met = [c for c in criteria if c["met"]]
            missing = [c for c in criteria if not c["met"]]
            if not missing:
                if category == "medical_necessity":
                    action = "appeal"
                    reasons.append(f"The clinical note meets all {len(criteria)} criteria of {policy['id']} "
                                   f"({policy['title']}).")
                    reasons.append("Appeal with the medical record attached.")
                else:
                    action, deadline = "corrected_claim", (dos + timedelta(days=profile.timely_filing_days)).isoformat()
                    em = denial["lines"][0]
                    fix = {"cpt": em["cpt"], "add_modifier": "25"}
                    reasons.append("The note documents a separately identifiable evaluation, so the E/M can be "
                                   f"billed with modifier 25 ({policy['id']}).")
                    reasons.append(f"Send a corrected claim (frequency 7) with modifier 25 on CPT {em['cpt']}.")
            else:
                action, deadline = "write_off", None
                reasons.append(f"The note doesn't show {len(missing)} of {len(criteria)} criteria of {policy['id']}: "
                               + "; ".join(c["text"].rstrip(".") for c in missing) + ".")
                if met:
                    reasons.append("Met: " + "; ".join(c["text"].rstrip(".") for c in met) + ".")
                reasons.append("An appeal would likely fail. Write off, or ask the physician whether documentation "
                               "is missing from the record.")

    elif category == "timely_filing":
        first = date.fromisoformat(context["first_submitted_on"])
        days = (first - dos).days
        if days <= profile.timely_filing_days:
            action = "appeal"
            reasons.append(f"The claim was first sent {days} days after the service, within {profile.name}'s "
                           f"{profile.timely_filing_days}-day limit. Appeal with the clearinghouse acceptance report.")
        else:
            action, deadline = "write_off", None
            reasons.append(f"The claim was first sent {days} days after the service; {profile.name} allows "
                           f"{profile.timely_filing_days}. There's no proof of earlier filing, so an appeal can't win.")
            reasons.append("Timely filing write-offs can't be billed to the patient.")

    elif category == "eligibility":
        action, deadline = "bill_patient", (remit_date + timedelta(days=30)).isoformat()
        reasons.append("The payer says coverage had ended before the date of service. The card in the packet shows "
                       "no end date, which usually means a retroactive termination by the employer.")
        reasons.append("Ask the patient about other coverage; if there's none, bill the patient.")

    else:
        reasons.append(f"Reason code {denial['group']}-{denial['code']} needs a person to decide.")

    if action == "write_off":
        win, expected = 0.0, 0.0
    elif action == "bill_patient":
        win = 0.4 if category == "eligibility" else WIN_PROBABILITY[action]
        expected = round(denial["amount"] * win, 2) if category == "patient_responsibility" else round(charges * win, 2)
    else:
        win = WIN_PROBABILITY[action]
        expected = round(expected_allowed * win, 2)

    return TriageResult(category, action, reasons, deadline, win, expected, policy["id"] if policy else None,
                        retrieved, criteria, summary, fix, result)


def denial_groups(claim_remit: dict) -> list[dict]:
    """Split a remittance claim into actionable denials: one per group + reason code.
    The routine contractual write-down (CO-45) isn't one."""
    from app.verticals.healthcare.carc import is_denial

    groups: dict[tuple[str, str], dict] = {}
    for line in claim_remit["lines"]:
        for adj in line["adjustments"]:
            if not is_denial(adj["group"], adj["code"]) or adj["amount"] <= 0:
                continue
            g = groups.setdefault((adj["group"], adj["code"]), {"group": adj["group"], "code": adj["code"],
                                                                "remarks": [], "lines": [], "amount": 0.0})
            g["lines"].append({"cpt": line["cpt"], "modifiers": line.get("modifiers", []), "charge": line["charge"],
                               "amount": adj["amount"]})
            g["amount"] = round(g["amount"] + adj["amount"], 2)
            g["remarks"] = sorted(set(g["remarks"]) | set(line.get("remarks", [])))
    for adj in claim_remit.get("adjustments", []):
        if is_denial(adj["group"], adj["code"]) and adj["amount"] > 0:
            groups.setdefault((adj["group"], adj["code"]), {"group": adj["group"], "code": adj["code"], "remarks": [],
                                                            "lines": [], "amount": adj["amount"]})
    return list(groups.values())


def same_member(record: dict, member_id: str) -> bool:
    return normalize_id(_value(record, "claim_form", "member_id")) == normalize_id(member_id)
