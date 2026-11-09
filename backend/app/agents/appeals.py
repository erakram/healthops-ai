"""Appeal Agent: drafts an appeal letter in which every argument is backed by the record.

The citation checks (quotes verified against the PDFs, unsupported paragraphs removed,
the evidence checklist) live in cited_writing.py and are shared with the Prior
Authorization Request Agent. The letter's header (payer, claim and member numbers,
dates, amounts) is filled in by code, never by the model, so identifiers can't be wrong.
"""

from datetime import date

from app.agents.cited_writing import (CitedDraft, criteria_evidence, norm, policy_text_of, write_cited)
from app.core.llm import LLM
from app.documents.pdf import ParsedPdf
from app.verticals.healthcare.carc import CARCS, RARCS
from app.verticals.healthcare.prompts import APPEAL_SYSTEM

AppealDraft = CitedDraft


def evidence_to_cite(facts: dict, triage: dict, docs: dict[str, tuple[int, ParsedPdf]]) -> list[dict]:
    """What the payer's reviewer will look for, as exact lines from the record. The model is
    asked to cite each one, and the draft is checked against this list before a person sends it."""
    items = []
    if triage.get("category") == "authorization" and "prior_authorization" in docs:
        _, pdf = docs["prior_authorization"]
        number = norm(facts.get("prior_auth_number") or "")
        lines = [" ".join(l.split()) for l in pdf.text.splitlines() if l.strip()]
        for line in lines:
            if (number and number in norm(line)) or line.lower().startswith(("valid from", "valid to")):
                items.append({"id": "A1" if number and number in norm(line) else "A2", "source": "prior_authorization",
                              "quote": line})
    return items + criteria_evidence(triage.get("criteria", []))


def draft_appeal(llm: LLM, facts: dict, triage: dict, docs: dict[str, tuple[int, ParsedPdf]], policy: dict | None,
                 model: str | None = None) -> CitedDraft:
    policy_text = policy_text_of(policy)
    sources = {kind: pdf.text[:8_000] for kind, (_, pdf) in docs.items()
               if kind in ("clinical_note", "prior_authorization", "claim_form")}
    payload = {
        "claim_facts": facts,
        "denial": {"reason": f"{facts['denial_code']}: {facts['denial_reason']}",
                   "remarks": [f"{r}: {RARCS.get(r, '')}" for r in facts.get("remark_codes", [])]},
        "analysis": {"reasons": triage.get("reasons", []),
                     "criteria": [{k: c.get(k) for k in ("id", "text", "met", "quote")} for c in triage.get("criteria", [])]},
        "policy": policy_text or None,
    }
    return write_cited(llm, APPEAL_SYSTEM, payload, docs, sources, policy_text, facts,
                       evidence_to_cite(facts, triage, docs), model)


def letter_header(facts: dict, provider: str, today: date) -> dict:
    """The parts of the letter that must be exactly right, filled in from the claim."""
    carc = CARCS.get(facts["denial_code"].split("-")[-1])
    return {
        "date": today.strftime("%B %d, %Y").replace(" 0", " "),
        "from": f"{provider}, Billing Office",
        "to": f"{facts['payer']}, Appeals and Reconsiderations",
        "subject": f"First-level appeal: claim {facts['claim_number']}",
        "reference": [
            ("Patient", facts["patient"]), ("Member ID", facts["member_id"]),
            ("Claim number", facts["claim_number"]), ("Payer claim number", facts["payer_claim_number"]),
            ("Date of service", date.fromisoformat(facts["service_date"]).strftime("%m/%d/%Y")),
            ("Denial", f"{facts['denial_code']}: {carc.description if carc else facts['denial_reason']}"),
            ("Amount in dispute", f"${facts['denied_amount']:,.2f}"),
        ],
        "salutation": "Dear Appeals Reviewer,",
        "closing": "Sincerely,",
        "signature": f"{provider} Billing Office",
    }


def letter_text(header: dict, paragraphs: list[dict], attachments: list[str]) -> str:
    lines = [header["date"], "", header["from"], header["to"], "", header["subject"]]
    lines += [f"{k}: {v}" for k, v in header["reference"]]
    lines += ["", header["salutation"], ""]
    for p in paragraphs:
        lines += [p["text"], ""]
    lines += [header["closing"], header["signature"]]
    if attachments:
        lines += ["", "Enclosures: " + ", ".join(attachments)]
    return "\n".join(lines)
