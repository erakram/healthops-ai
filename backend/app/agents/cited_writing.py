"""Letters whose every argument is backed by the record. Shared by the Appeal Agent and the
Prior Authorization Request Agent.

The model writes paragraphs and, for each, the exact quotes it relies on. Then:
  1. each quote is looked up in its source (the PDF's words, or the policy text); quotes
     that aren't there are dropped,
  2. an argument paragraph left with no verified quote is removed,
  3. any paragraph mentioning a date, amount, code or ID that isn't in the facts or the
     sources is removed.
Removed paragraphs are shown to a person with the reason. A checklist compares the
draft with the evidence the payer will look for, so a weak letter is caught before it's sent.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date

from app.core.llm import LLM, LLMResult
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare.schema import DOCUMENT_TYPES

SOURCE_LABELS = {"policy": "Payer policy", **{k: v["label"] for k, v in DOCUMENT_TYPES.items()}}
_FACT = re.compile(r"\S*\d\S*")  # any token containing a digit: dates, amounts, codes, IDs
_ALNUM = re.compile(r"[^a-z0-9]")
_NUMBERED = re.compile(r"^(.*)_(\d+)$")


@dataclass
class CitedDraft:
    paragraphs: list[dict]
    removed: list[dict]
    stats: dict
    llm: LLMResult | None = None
    attachments: list[str] = field(default_factory=list)
    checklist: list[dict] = field(default_factory=list)  # evidence the payer will look for, and whether it's cited

    def as_json(self) -> dict:
        return {"paragraphs": self.paragraphs, "removed": self.removed, "stats": self.stats,
                "attachments": self.attachments, "checklist": self.checklist}


def norm(text: str) -> str:
    return _ALNUM.sub("", text.lower())


def source_label(source: str) -> str:
    """'clinical_note' -> 'Clinical note'; a second note 'clinical_note_2' -> 'Clinical note (addendum)'."""
    match = _NUMBERED.match(source)
    if match and match.group(1) in SOURCE_LABELS:
        return f"{SOURCE_LABELS[match.group(1)]} (addendum)"
    return SOURCE_LABELS.get(source, source.replace("_", " ").capitalize())


def date_forms(iso: str | None) -> list[str]:
    if not iso:
        return []
    d = date.fromisoformat(iso)
    return [iso, d.strftime("%m/%d/%Y"), d.strftime("%B %d, %Y").replace(" 0", " "), d.strftime("%B %Y")]


def facts_text(facts: dict) -> str:
    """Everything a letter may mention: the facts, with dates and amounts in every format a writer might use."""
    parts = [json.dumps(facts)]
    for key, value in facts.items():
        if key.endswith("_date") and isinstance(value, str):
            try:
                parts += date_forms(value)
            except ValueError:
                pass
        if key.endswith("_amount") and isinstance(value, (int, float)):
            parts += [f"${value:,.2f}", f"{value:.2f}"]
    return " ".join(parts)


def unsupported_facts(text: str, known: str, known_tokens: set[str]) -> list[str]:
    missing = []
    for token in _FACT.findall(text):
        n = norm(token)
        if not n or n in known_tokens:
            continue
        if len(n) >= 3 and n in known:
            continue
        if n.isdigit() and len(n) <= 2:  # "4 weeks", "2 views": too short to be an identifier
            continue
        missing.append(token.strip(".,;:()"))
    return missing


def verify_citation(citation: dict, docs: dict[str, tuple[int, ParsedPdf]], policy_text: str) -> dict:
    source = str(citation.get("source") or "")
    quote = str(citation.get("quote") or "").strip()
    out = {"source": source, "label": source_label(source), "quote": quote, "verified": False, "evidence": None}
    if not quote:
        return out
    if source == "policy":
        out["verified"] = len(norm(quote)) >= 12 and norm(quote) in norm(policy_text)
    elif source in docs:
        doc_id, pdf = docs[source]
        hit = locate(pdf, quote)
        if hit:
            out.update(verified=True, quote=hit["text"],
                       evidence={"document_id": doc_id, **hit, "label": source_label(source)})
    return out


def _cited(item: dict, citations: list[dict]) -> bool:
    want = norm(item["quote"])
    return any(c["source"] == item["source"] and (want in norm(c["quote"]) or norm(c["quote"]) in want)
               and len(norm(c["quote"])) >= min(len(want), 8) for c in citations)


def checklist(items: list[dict], paragraphs: list[dict]) -> list[dict]:
    citations = [c for p in paragraphs for c in p.get("citations", [])]
    return [{**item, "cited": _cited(item, citations)} for item in items]


def policy_text_of(policy: dict | None) -> str:
    if not policy:
        return ""
    return f"{policy['id']} {policy['title']}. {policy['body']} " + " ".join(
        f"{c['id']}: {c['text']}" for c in policy["criteria"])


def criteria_evidence(criteria: list[dict]) -> list[dict]:
    """Met criteria as evidence to cite: the quote and the document it was found in."""
    return [{"id": c["id"], "source": c.get("source", "clinical_note"), "quote": c["quote"]}
            for c in criteria if c.get("met") and c.get("evidence")]


def write_cited(llm: LLM, system: str, payload: dict, docs: dict[str, tuple[int, ParsedPdf]], sources: dict[str, str],
                policy_text: str, facts: dict, evidence: list[dict], model: str | None = None) -> CitedDraft:
    """Ask for a cited letter, then keep only what the record supports."""
    request = {**payload, "evidence_to_cite": [{k: e[k] for k in ("source", "quote")} for e in evidence],
               "sources": sources}
    result = llm.complete_json(system, json.dumps(request, indent=1), model=model)

    known = facts_text(facts) + " " + " ".join(sources.values()) + " " + policy_text
    known_norm = norm(known)
    known_tokens = {norm(t) for t in known.split()} | {norm(t) for t in _FACT.findall(known)}
    paragraphs, removed = [], []
    total = verified = 0
    raw = result.data.get("paragraphs") or []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        text = " ".join(str(item["text"]).split())
        kind = "request" if item.get("kind") == "request" or index == len(raw) - 1 and not item.get("citations") \
            else "argument"
        citations = [verify_citation(c, docs, policy_text) for c in item.get("citations") or [] if isinstance(c, dict)]
        total += len(citations)
        good = [c for c in citations if c["verified"]]
        verified += len(good)
        invented = unsupported_facts(text, known_norm, known_tokens)
        if invented:
            removed.append({"text": text, "reason": f"Mentions {', '.join(repr(t) for t in invented[:3])}, "
                                                     "which isn't in the record or its documents."})
        elif kind == "argument" and not good:
            removed.append({"text": text, "reason": "None of its quotes could be found in the documents."
                                                    if citations else "It cites nothing from the record."})
        else:
            paragraphs.append({"kind": kind, "text": text, "citations": good,
                               "dropped_citations": len(citations) - len(good)})

    attachments = sorted({c["label"] for p in paragraphs for c in p["citations"] if c["source"] != "policy"})
    checks = checklist(evidence, paragraphs)
    stats = {"citations": total, "verified_citations": verified,
             "citation_rate": round(verified / total, 3) if total else None,
             "paragraphs": len(paragraphs), "removed_paragraphs": len(removed),
             "invented_fact_paragraphs": sum(1 for r in removed if r["reason"].startswith("Mentions")),
             "evidence_cited": sum(1 for c in checks if c["cited"]), "evidence_required": len(checks)}
    return CitedDraft(paragraphs, removed, stats, result, attachments, checks)
