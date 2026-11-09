"""Coding Agent: suggests ICD-10-CM and CPT codes for an encounter, each backed by the note.

Two model calls with code search in between, so the model never types a code from memory:
  1. read: what was documented (diagnoses, procedures, the E/M elements), each with a quote
  2. search: BM25 over the 74,000 ICD-10-CM codes (and the CPT list) for each item
  3. select: the model picks one code per item from its candidates; anything else is rejected
Then rules: the E/M level from the documented elements (coding_rules.em_level), modifier
25, and the coding checks. Quotes are located in the PDF so every code can be highlighted.
"""

import json
from dataclasses import dataclass, field

from app.core.llm import LLM, LLMResult
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare.codesets import CPT_CODES, EM_BUNDLED_WITH, dotted, icd10, search_cpt, search_icd10
from app.verticals.healthcare.coding_rules import coding_edits, em_level
from app.verticals.healthcare.prompts import CODING_EXTRACT_SYSTEM, CODING_SELECT_SYSTEM

ICD_CANDIDATES = 12
CPT_CANDIDATES = 6


@dataclass
class CodingResult:
    diagnoses: list[dict]
    procedures: list[dict]  # CPT lines, the E/M visit first when there is one
    em: dict | None
    edits: list[dict]
    documented: dict  # what the model read, with evidence
    stats: dict
    llm: list[LLMResult] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"diagnoses": self.diagnoses, "procedures": self.procedures, "em": self.em, "edits": self.edits,
                "documented": self.documented, "stats": self.stats}


def _evidence(note: tuple[int, ParsedPdf], quote: str | None) -> dict | None:
    doc_id, pdf = note
    hit = locate(pdf, quote or "") if quote and len(quote.split()) >= 3 else None
    return {"document_id": doc_id, **hit, "label": "Encounter note"} if hit else None


def _fused_icd_search(item: dict) -> list[dict]:
    """Search three ways (the model's search terms, its wording of the condition, the note's own
    sentence) and merge by reciprocal rank fusion, so one odd phrasing can't hide the right code."""
    queries = [q for q in (item.get("search"), item.get("condition"), item.get("quote")) if q and str(q).strip()]
    scores: dict[str, float] = {}
    hits: dict[str, dict] = {}
    for query in dict.fromkeys(str(q) for q in queries):
        for rank, hit in enumerate(search_icd10(query, ICD_CANDIDATES)):
            scores[hit["code"]] = scores.get(hit["code"], 0.0) + 1 / (60 + rank)
            hits.setdefault(hit["code"], hit)
    return [hits[c] for c in sorted(scores, key=lambda c: -scores[c])[:ICD_CANDIDATES]]


def code_encounter(llm: LLM, note: tuple[int, ParsedPdf], model: str | None = None) -> CodingResult:
    _, pdf = note
    read = llm.complete_json(CODING_EXTRACT_SYSTEM, pdf.text[:12_000], model=model)
    data = read.data
    diagnoses_in = [d for d in data.get("diagnoses") or [] if isinstance(d, dict) and (d.get("condition") or d.get("search"))]
    procedures_in = [p for p in data.get("procedures") or [] if isinstance(p, dict) and (p.get("service") or p.get("search"))]

    dx_candidates = [_fused_icd_search(d) for d in diagnoses_in]
    px_candidates = [search_cpt(f"{p.get('search') or ''} {p.get('service') or ''}", CPT_CANDIDATES) for p in procedures_in]
    payload = {
        "diagnoses": [{"index": i, "documented": d.get("condition"), "quote": d.get("quote"),
                       "candidates": [{"code": c["code"], "description": c["description"]} for c in cands]}
                      for i, (d, cands) in enumerate(zip(diagnoses_in, dx_candidates))],
        "procedures": [{"index": i, "documented": p.get("service"), "quote": p.get("quote"),
                        "candidates": [{"code": c["code"], "description": c["description"]} for c in cands]}
                       for i, (p, cands) in enumerate(zip(procedures_in, px_candidates))],
    }
    results = [read]
    picks = {"diagnoses": {}, "procedures": {}}
    if payload["diagnoses"] or payload["procedures"]:
        select = llm.complete_json(CODING_SELECT_SYSTEM, json.dumps(payload, indent=1), model=model)
        results.append(select)
        for kind in ("diagnoses", "procedures"):
            for p in select.data.get(kind) or []:
                if isinstance(p, dict) and isinstance(p.get("index"), int):
                    picks[kind][p["index"]] = p

    rejected = 0
    diagnoses = []
    for i, (d, cands) in enumerate(zip(diagnoses_in, dx_candidates)):
        pick = picks["diagnoses"].get(i, {})
        code = dotted(pick["code"]) if pick.get("code") else None
        allowed = {c["code"] for c in cands}
        if code and code not in allowed:
            rejected += 1  # the model answered with a code that wasn't offered: never trusted
            code = None
        if not code or any(x["code"] == code for x in diagnoses):
            continue
        diagnoses.append({"code": code, "description": icd10()[code], "documented": d.get("condition"),
                          "quote": d.get("quote"), "evidence": _evidence(note, d.get("quote")),
                          "reason": pick.get("reason"), "candidates": cands[:6]})

    procedures = []
    for i, (p, cands) in enumerate(zip(procedures_in, px_candidates)):
        pick = picks["procedures"].get(i, {})
        code = str(pick.get("code") or "").strip() or None
        if code and code not in {c["code"] for c in cands}:
            rejected += 1
            code = None
        if not code:
            continue
        units = p.get("units") if isinstance(p.get("units"), int) and p.get("units") > 0 else 1
        procedures.append({"code": code, "description": CPT_CODES[code][0], "modifiers": [], "units": units,
                           "documented": p.get("service"), "quote": p.get("quote"),
                           "evidence": _evidence(note, p.get("quote")), "reason": pick.get("reason"),
                           "candidates": cands})

    visit = data.get("visit") or {}
    separate = data.get("separate_em") or {}
    separate_documented = bool(separate.get("documented")) and _evidence(note, separate.get("quote")) is not None
    mdm = {k: {**(data.get("mdm") or {}).get(k, {}), "evidence": _evidence(note, ((data.get("mdm") or {}).get(k) or {}).get("quote"))}
           for k in ("problems", "data", "risk")}
    time_info = data.get("time_minutes") or {}
    minutes = time_info.get("value") if isinstance(time_info.get("value"), int) else None
    if minutes is not None and _evidence(note, time_info.get("quote")) is None:
        minutes = None  # time counts only if the note states it
    em = None
    if visit.get("type") == "office_visit":
        em = em_level(str(visit.get("patient_status") or "established"), mdm, minutes)
        modifiers = ["25"] if separate_documented and any(p["code"] in EM_BUNDLED_WITH for p in procedures) else []
        procedures.insert(0, {"code": em["code"], "description": CPT_CODES[em["code"]][0], "modifiers": modifiers,
                              "units": 1, "documented": "Office visit", "quote": visit.get("quote"),
                              "evidence": _evidence(note, visit.get("quote")), "reason": em["explanation"],
                              "candidates": []})

    edits = coding_edits([d["code"] for d in diagnoses], procedures, pdf.text, separate_documented)
    quotes = [x.get("evidence") for x in diagnoses + procedures]
    stats = {"diagnoses": len(diagnoses), "procedures": len(procedures), "rejected_choices": rejected,
             "grounded": sum(1 for q in quotes if q), "quotes": len(quotes),
             "searched": [[c["code"] for c in cands] for cands in dx_candidates + px_candidates],
             "queries": [{k: d.get(k) for k in ("condition", "search")} for d in diagnoses_in]}
    documented = {"visit": {**visit, "evidence": _evidence(note, visit.get("quote"))},
                  "time": {**time_info, "value": minutes, "evidence": _evidence(note, time_info.get("quote"))},
                  "mdm": mdm, "separate_em": {**separate, "documented": separate_documented,
                                              "evidence": _evidence(note, separate.get("quote"))}}
    return CodingResult(diagnoses, procedures, em, edits, documented, stats, results)
