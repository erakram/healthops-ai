"""Document agent: classifies each document and extracts its fields with evidence.

The LLM returns each value with the text it read it from. That quote is then searched
for in the PDF's words (documents/pdf.py). Values whose quote is found get high
confidence and a highlight box; values whose quote isn't found are kept but marked
unverified, so a reviewer knows to check them.
"""

import json
import re
from dataclasses import dataclass, field

from app.core.llm import LLM, LLMResult
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare.prompts import EXTRACTION_SYSTEM
from app.verticals.healthcare.schema import DOCUMENT_TYPES, normalize_value

MAX_DOCUMENT_CHARS = 24_000  # about 6k tokens: enough for every document type in this pack
GROUNDED, UNGROUNDED = 0.95, 0.55

# A cheap first line of defence; the prompt also asks the model to report these.
INJECTION_PATTERNS = re.compile(
    r"(ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(rules|instructions)|"
    r"you are now|system prompt|approve this claim|mark (this|the) claim as (approved|valid)|"
    r"do not flag|override (the )?(validation|review))", re.IGNORECASE)


@dataclass
class ExtractionResult:
    document_type: str
    fields: dict  # name -> {value, confidence, evidence}
    suspicious_text: str | None = None
    llm: LLMResult | None = None
    notes: list[str] = field(default_factory=list)


def _grounded(parsed: ParsedPdf, document_id: int, quote: str | None, value) -> tuple[float, dict | None]:
    hit = locate(parsed, quote or "") or locate(parsed, str(value or ""))
    if hit:
        return GROUNDED, {"document_id": document_id, **hit}
    return UNGROUNDED, None


def extract_document(llm: LLM, parsed: ParsedPdf, document_id: int, model: str | None = None) -> ExtractionResult:
    result = llm.complete_json(EXTRACTION_SYSTEM, parsed.text[:MAX_DOCUMENT_CHARS], model=model)
    data = result.data
    kind = data.get("document_type") if data.get("document_type") in DOCUMENT_TYPES else "other"
    out = ExtractionResult(document_type=kind, fields={}, llm=result)

    if kind != "other":
        spec = DOCUMENT_TYPES[kind]
        raw_fields = data.get("fields") or {}
        for field_spec in spec["fields"]:
            raw = raw_fields.get(field_spec.name)
            if not isinstance(raw, dict) or raw.get("value") in (None, ""):
                continue
            confidence, evidence = _grounded(parsed, document_id, raw.get("quote"), raw.get("value"))
            out.fields[field_spec.name] = {
                "value": normalize_value(field_spec.type, raw["value"]),
                "raw": raw["value"],
                "confidence": confidence,
                "evidence": evidence,
            }

        if "line_items" in spec["lists"]:
            items = []
            for item in data.get("line_items") or []:
                if not isinstance(item, dict):
                    continue
                _, evidence = _grounded(parsed, document_id, item.get("quote"), item.get("cpt"))
                modifiers = [str(m).strip() for m in item.get("modifiers") or [] if str(m).strip()]
                items.append({"cpt": str(item.get("cpt") or "").strip(), "modifiers": modifiers,
                              "description": item.get("description"), "units": item.get("units"),
                              "charge": normalize_value("money", item.get("charge")), "evidence": evidence})
            out.fields["line_items"] = {"value": items, "confidence": _list_confidence(items)}

        for list_name in ("diagnosis_codes", "approved_cpt_codes"):
            if list_name not in spec["lists"]:
                continue
            codes = []
            for entry in data.get(list_name) or []:
                entry = _unwrap(entry)
                code = entry.get("code") if isinstance(entry, dict) else entry
                if not code:
                    continue
                _, evidence = _grounded(parsed, document_id,
                                        entry.get("quote") if isinstance(entry, dict) else None, code)
                codes.append({"code": str(code).strip().upper(), "evidence": evidence})
            out.fields[list_name] = {"value": codes, "confidence": _list_confidence(codes)}

    # Report injection attempts whether the model noticed them or our pattern did.
    pattern_hit = INJECTION_PATTERNS.search(parsed.text)
    reported = data.get("suspicious_instructions")
    if pattern_hit or reported:
        out.suspicious_text = reported if isinstance(reported, str) and reported else pattern_hit.group(0)
    return out


def _unwrap(entry):
    """Models occasionally return a list item as a JSON string ('{"code": "M17.11", ...}')
    instead of an object. Seen in evals; parse it rather than keep the string as the code."""
    if isinstance(entry, str) and entry.strip().startswith("{"):
        try:
            parsed = json.loads(entry)
            return parsed if isinstance(parsed, dict) else entry
        except json.JSONDecodeError:
            return entry
    return entry


def _list_confidence(items: list[dict]) -> float:
    if not items:
        return 0.0
    return round(sum(GROUNDED if i.get("evidence") else UNGROUNDED for i in items) / len(items), 2)
