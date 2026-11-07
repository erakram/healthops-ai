"""Appeal Agent: drafts an appeal letter in which every argument is backed by the record.

The model writes paragraphs and, for each, the exact quotes it relies on. Then:
  1. each quote is looked up in its source (the PDF's words, or the policy text); quotes
     that aren't there are dropped,
  2. an argument paragraph left with no verified quote is removed,
  3. any paragraph mentioning a date, amount, code or ID that isn't in the claim or its
     documents is removed.
Removed paragraphs are shown to the biller with the reason. The letter's header (payer,
claim and member numbers, dates, amounts) is filled in by code, never by the model, so
identifiers can't be wrong.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date

from app.core.llm import LLM, LLMResult
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare.carc import CARCS, RARCS
from app.verticals.healthcare.prompts import APPEAL_SYSTEM
from app.verticals.healthcare.schema import DOCUMENT_TYPES

SOURCE_LABELS = {"policy": "Payer policy", **{k: v["label"] for k, v in DOCUMENT_TYPES.items()}}
_FACT = re.compile(r"\S*\d\S*")  # any token containing a digit: dates, amounts, codes, IDs
_ALNUM = re.compile(r"[^a-z0-9]")


@dataclass
class AppealDraft:
    paragraphs: list[dict]
    removed: list[dict]
    stats: dict
    llm: LLMResult | None = None
    attachments: list[str] = field(default_factory=list)
    checklist: list[dict] = field(default_factory=list)  # evidence the payer will look for, and whether it's cited

    def as_json(self) -> dict:
        return {"paragraphs": self.paragraphs, "removed": self.removed, "stats": self.stats,
                "attachments": self.attachments, "checklist": self.checklist}


def _norm(text: str) -> str:
    return _ALNUM.sub("", text.lower())


def _date_forms(iso: str | None) -> list[str]:
    if not iso:
        return []
    d = date.fromisoformat(iso)
    return [iso, d.strftime("%m/%d/%Y"), d.strftime("%B %d, %Y").replace(" 0", " "), d.strftime("%B %Y")]


def facts_text(facts: dict) -> str:
    """Everything the letter may mention: the claim facts in every format a writer might use."""
    parts = [json.dumps(facts)]
    for key in ("service_date", "remit_date"):
        parts += _date_forms(facts.get(key))
    for key in ("denied_amount", "billed_amount"):
        if facts.get(key) is not None:
            parts += [f"${facts[key]:,.2f}", f"{facts[key]:.2f}"]
    return " ".join(parts)


def unsupported_facts(text: str, known: str, known_tokens: set[str]) -> list[str]:
    missing = []
    for token in _FACT.findall(text):
        norm = _norm(token)
        if not norm or norm in known_tokens:
            continue
        if len(norm) >= 3 and norm in known:
            continue
        if norm.isdigit() and len(norm) <= 2:  # "4 weeks", "2 views": too short to be an identifier
            continue
        missing.append(token.strip(".,;:()"))
    return missing


def _verify(citation: dict, docs: dict[str, tuple[int, ParsedPdf]], policy_text: str) -> dict:
    source = str(citation.get("source") or "")
    quote = str(citation.get("quote") or "").strip()
    out = {"source": source, "label": SOURCE_LABELS.get(source, source), "quote": quote, "verified": False,
           "evidence": None}
    if not quote:
        return out
    if source == "policy":
        out["verified"] = len(_norm(quote)) >= 12 and _norm(quote) in _norm(policy_text)
    elif source in docs:
        doc_id, pdf = docs[source]
        hit = locate(pdf, quote)
        if hit:
            out.update(verified=True, quote=hit["text"],
                       evidence={"document_id": doc_id, **hit, "label": SOURCE_LABELS.get(source, source)})
    return out


def evidence_to_cite(facts: dict, triage: dict, docs: dict[str, tuple[int, ParsedPdf]]) -> list[dict]:
    """What the payer's reviewer will look for, as exact lines from the record. The model is
    asked to cite each one, and the draft is checked against this list before a person sends it."""
    items = []
    if triage.get("category") == "authorization" and "prior_authorization" in docs:
        _, pdf = docs["prior_authorization"]
        number = _norm(facts.get("prior_auth_number") or "")
        lines = [" ".join(l.split()) for l in pdf.text.splitlines() if l.strip()]
        for line in lines:
            if (number and number in _norm(line)) or line.lower().startswith(("valid from", "valid to")):
                items.append({"id": "A1" if number and number in _norm(line) else "A2", "source": "prior_authorization",
                              "quote": line})
    for c in triage.get("criteria", []):
        if c.get("met") and c.get("evidence"):
            items.append({"id": c["id"], "source": "clinical_note", "quote": c["quote"]})
    return items


def _cited(item: dict, citations: list[dict]) -> bool:
    want = _norm(item["quote"])
    return any(c["source"] == item["source"] and (want in _norm(c["quote"]) or _norm(c["quote"]) in want)
               and len(_norm(c["quote"])) >= min(len(want), 8) for c in citations)


def checklist(items: list[dict], paragraphs: list[dict]) -> list[dict]:
    citations = [c for p in paragraphs for c in p.get("citations", [])]
    return [{**item, "cited": _cited(item, citations)} for item in items]


def draft_appeal(llm: LLM, facts: dict, triage: dict, docs: dict[str, tuple[int, ParsedPdf]], policy: dict | None,
                 model: str | None = None) -> AppealDraft:
    policy_text = ""
    if policy:
        policy_text = f"{policy['id']} {policy['title']}. {policy['body']} " + " ".join(
            f"{c['id']}: {c['text']}" for c in policy["criteria"])
    sources = {kind: pdf.text[:8_000] for kind, (_, pdf) in docs.items()
               if kind in ("clinical_note", "prior_authorization", "claim_form")}
    payload = {
        "claim_facts": facts,
        "denial": {"reason": f"{facts['denial_code']}: {facts['denial_reason']}",
                   "remarks": [f"{r}: {RARCS.get(r, '')}" for r in facts.get("remark_codes", [])]},
        "analysis": {"reasons": triage.get("reasons", []),
                     "criteria": [{k: c.get(k) for k in ("id", "text", "met", "quote")} for c in triage.get("criteria", [])]},
        "policy": policy_text or None,
        "evidence_to_cite": [{k: e[k] for k in ("source", "quote")} for e in evidence_to_cite(facts, triage, docs)],
        "sources": sources,
    }
    result = llm.complete_json(APPEAL_SYSTEM, json.dumps(payload, indent=1), model=model)

    known = facts_text(facts) + " " + " ".join(sources.values()) + " " + policy_text
    known_norm = _norm(known)
    known_tokens = {_norm(t) for t in known.split()} | {_norm(t) for t in _FACT.findall(known)}
    paragraphs, removed = [], []
    total = verified = 0
    raw = result.data.get("paragraphs") or []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        text = " ".join(str(item["text"]).split())
        kind = "request" if item.get("kind") == "request" or index == len(raw) - 1 and not item.get("citations") \
            else "argument"
        citations = [_verify(c, docs, policy_text) for c in item.get("citations") or [] if isinstance(c, dict)]
        total += len(citations)
        good = [c for c in citations if c["verified"]]
        verified += len(good)
        invented = unsupported_facts(text, known_norm, known_tokens)
        if invented:
            removed.append({"text": text, "reason": f"Mentions {', '.join(repr(t) for t in invented[:3])}, "
                                                     "which isn't in the claim or its documents."})
        elif kind == "argument" and not good:
            removed.append({"text": text, "reason": "None of its quotes could be found in the documents."
                                                    if citations else "It cites nothing from the record."})
        else:
            dropped = len(citations) - len(good)
            paragraphs.append({"kind": kind, "text": text, "citations": good, "dropped_citations": dropped})

    attachments = sorted({c["label"] for p in paragraphs for c in p["citations"] if c["source"] != "policy"})
    checks = checklist(evidence_to_cite(facts, triage, docs), paragraphs)
    stats = {"citations": total, "verified_citations": verified,
             "citation_rate": round(verified / total, 3) if total else None,
             "paragraphs": len(paragraphs), "removed_paragraphs": len(removed),
             "invented_fact_paragraphs": sum(1 for r in removed if r["reason"].startswith("Mentions")),
             "evidence_cited": sum(1 for c in checks if c["cited"]), "evidence_required": len(checks)}
    return AppealDraft(paragraphs, removed, stats, result, attachments, checks)


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
