"""Prior authorization agents.

  check_requirement      rules: does this payer need authorization for these services, and
                         is the member covered on the scheduled date? (no LLM)
  assess_documentation   Clinical Criteria Agent: does the documentation meet each criterion
                         of the payer's policy? A criterion is met only with a quote found in
                         a note. Unmet criteria become precise asks for the physician.
  draft_request          Request Agent: the medical necessity summary, every argument cited
                         and verified (shared with the Appeal Agent, see cited_writing.py)
"""

import json
from dataclasses import dataclass, field
from datetime import date

from app.agents.cited_writing import CitedDraft, criteria_evidence, policy_text_of, source_label, write_cited
from app.core.llm import LLM, LLMResult
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare import payer
from app.verticals.healthcare.prompts import AUTH_ASSESS_SYSTEM, REQUEST_SYSTEM

ACTIONS = {
    "submit": "Submit to the payer",
    "request_documentation": "Ask the physician for documentation",
    "do_not_submit": "Don't submit: criteria not met",
    "none": "No authorization needed",
    "ineligible": "Coverage inactive",
}


def _value(record: dict, doc: str, name: str):
    return ((record.get(doc) or {}).get(name) or {}).get("value")


def services_of(record: dict) -> list[dict]:
    return [{"cpt": str(li.get("cpt") or "").strip(), "description": li.get("description"), "units": li.get("units") or 1}
            for li in (record.get("service_order") or {}).get("line_items", {}).get("value", []) if li.get("cpt")]


def check_requirement(record: dict, today: date) -> dict:
    services = services_of(record)
    payer_name = _value(record, "service_order", "payer_name") or _value(record, "insurance_card", "payer_name")
    member = _value(record, "service_order", "member_id") or _value(record, "insurance_card", "member_id")
    scheduled = _value(record, "service_order", "scheduled_date")
    on = date.fromisoformat(scheduled) if scheduled else today
    elig = payer.eligibility(payer_name, member, on)
    needed = [s for s in services if payer.requires_authorization(s["cpt"])]
    policies = [p for p in (payer.medical_policy_for(s["cpt"]) for s in needed) if p]
    reasons = []
    if not elig["active"]:
        reasons.append(f"{elig['payer']} reports coverage ended {elig['terminated_on']}, before the scheduled date. "
                       "Confirm other insurance with the patient before scheduling.")
    if needed:
        reasons.append(f"{elig['payer']} requires prior authorization for "
                       + ", ".join(f"CPT {s['cpt']} ({s['description']})" for s in needed) + ".")
    elif services:
        reasons.append("None of the ordered services needs prior authorization with this payer.")
    else:
        reasons.append("The order lists no services that could be read.")
    return {"required": bool(needed), "eligible": elig["active"], "eligibility": elig, "services": services,
            "required_cpts": [s["cpt"] for s in needed], "policy_id": policies[0]["id"] if policies else None,
            "reasons": reasons}


@dataclass
class Assessment:
    criteria: list[dict]
    missing: list[dict]
    action: str
    reasons: list[str]
    summary: str = ""
    llm: LLMResult | None = None
    extra: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"criteria": self.criteria, "missing": self.missing, "action": self.action, "reasons": self.reasons,
                "summary": self.summary, **self.extra}


def assess_documentation(llm: LLM, policy: dict, services: list[dict], notes: dict[str, tuple[int, ParsedPdf]],
                         model: str | None = None) -> Assessment:
    """notes: {"clinical_note": (doc_id, pdf), "clinical_note_2": ...} (later ones are addenda)."""
    documentation = "\n\n".join(f"=== {source_label(source)} ===\n{pdf.text[:8_000]}" for source, (_, pdf) in notes.items())
    payload = {"requested_services": [f"{s['cpt']} {s['description'] or ''}".strip() for s in services],
               "policy": {"id": policy["id"], "title": policy["title"],
                          "criteria": [{"id": c["id"], "text": c["text"]} for c in policy["criteria"]]},
               "documentation": documentation}
    result = llm.complete_json(AUTH_ASSESS_SYSTEM, json.dumps(payload, indent=1), model=model)
    answers = {str(a.get("id")): a for a in result.data.get("criteria") or [] if isinstance(a, dict)}

    criteria = []
    for c in policy["criteria"]:
        answer = answers.get(c["id"], {})
        quote = str(answer.get("quote") or "")
        said_met = answer.get("met") is True
        found = None
        if quote:
            for source, (doc_id, pdf) in notes.items():
                hit = locate(pdf, quote)
                if hit:
                    found = (source, doc_id, hit)
                    break
        criteria.append({
            "id": c["id"], "text": c["text"], "met": said_met and found is not None, "model_said_met": said_met,
            "quote": found[2]["text"] if found else quote, "explanation": str(answer.get("explanation") or ""),
            "source": found[0] if found else None,
            "evidence": {"document_id": found[1], **found[2], "label": source_label(found[0])} if found else None,
        })
    return _recommend(policy, criteria, str(result.data.get("summary") or ""), result)


def _recommend(policy: dict, criteria: list[dict], summary: str, result: LLMResult | None) -> Assessment:
    met = [c for c in criteria if c["met"]]
    missing = [{"id": c["id"], "text": c["text"],
                "ask": c["explanation"] if c["explanation"] and not c["model_said_met"] else f"Document: {c['text']}",
                "unverified_quote": c["quote"] if c["model_said_met"] else None}
               for c in criteria if not c["met"]]
    if not missing:
        return Assessment(criteria, [], "submit",
                          [f"The documentation meets all {len(criteria)} criteria of {policy['id']} ({policy['title']}).",
                           "Review the drafted request and submit it."], summary, result)
    gaps = "; ".join(m["text"].rstrip(".") for m in missing)
    if met:
        return Assessment(criteria, missing, "request_documentation",
                          [f"{len(met)} of {len(criteria)} criteria of {policy['id']} are documented. Missing: {gaps}.",
                           "Ask the ordering physician for an addendum before submitting; a request sent now would "
                           "likely be pended for more information."], summary, result)
    return Assessment(criteria, missing, "do_not_submit",
                      [f"None of the {len(criteria)} criteria of {policy['id']} ({policy['title']}) is documented: {gaps}.",
                       "Discuss with the ordering physician: the procedure may not meet the payer's policy, or the "
                       "record is incomplete."], summary, result)


def draft_request(llm: LLM, facts: dict, assessment: dict, docs: dict[str, tuple[int, ParsedPdf]], policy: dict | None,
                  model: str | None = None) -> CitedDraft:
    policy_text = policy_text_of(policy)
    sources = {kind: pdf.text[:8_000] for kind, (_, pdf) in docs.items()
               if kind.startswith("clinical_note") or kind == "service_order"}
    payload = {"order_facts": facts,
               "analysis": {"reasons": assessment.get("reasons", []),
                            "criteria": [{k: c.get(k) for k in ("id", "text", "met", "quote", "source")}
                                         for c in assessment.get("criteria", [])]},
               "policy": policy_text or None}
    return write_cited(llm, REQUEST_SYSTEM, payload, docs, sources, policy_text, facts,
                       criteria_evidence(assessment.get("criteria", [])), model)


def request_header(facts: dict, today: date) -> dict:
    """The identifying parts of the request, filled in from the order, never by the model."""
    return {
        "date": today.strftime("%B %d, %Y").replace(" 0", " "),
        "from": f"{facts['provider']}, Prior Authorization Office",
        "to": f"{facts['payer']}, Utilization Management",
        "subject": f"Prior authorization request {facts['request_number']}"
                   + (" (URGENT, expedited review requested)" if facts["urgency"] == "urgent" else ""),
        "reference": [
            ("Patient", facts["patient"]), ("Member ID", facts["member_id"]), ("Order number", facts["order_number"]),
            ("Requested", ", ".join(f"CPT {s['cpt']} {s['description'] or ''}".strip() for s in facts["services"])),
            ("Diagnosis", ", ".join(facts["diagnosis_codes"]) or "Not given"),
            ("Scheduled date", date.fromisoformat(facts["scheduled_date"]).strftime("%m/%d/%Y")
             if facts.get("scheduled_date") else "Not scheduled"),
        ],
        "salutation": "Dear Utilization Review Team,",
        "closing": "Sincerely,",
        "signature": f"{facts['provider']} Prior Authorization Office",
    }
