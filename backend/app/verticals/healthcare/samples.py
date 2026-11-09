"""Access to the generated sample cases (scripts/generate_samples.py)."""

import json
from pathlib import Path

from app.config import BACKEND_ROOT
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare.schema import DOCUMENT_TYPES, normalize_value

CASES_DIR = BACKEND_ROOT / "sample_data" / "cases"
FILE_KINDS = {"service_order.pdf": "service_order", "claim_form.pdf": "claim_form", "itemized_invoice.pdf": "invoice", "insurance_card.pdf": "insurance_card",
              "clinical_note.pdf": "clinical_note", "prior_authorization.pdf": "prior_authorization"}


def list_cases(prefix: str = "case_") -> list[dict]:
    if not CASES_DIR.exists():
        return []
    cases = []
    for folder in sorted(CASES_DIR.iterdir()):
        if folder.is_dir() and folder.name.startswith(prefix) and (folder / "expected.json").exists():
            expected = json.loads((folder / "expected.json").read_text())
            expected["files"] = sorted(p.name for p in folder.glob("*.pdf"))
            expected["path"] = str(folder)
            cases.append(expected)
    return cases


def get_case(case_id: str) -> dict | None:
    return next((c for c in list_cases("") if c["case_id"] == case_id), None)


def truth_to_extracted(record: dict, parsed: dict[str, tuple[int, ParsedPdf]]) -> dict:
    """Turn an answer key into the same shape the Document Agent produces, with evidence
    boxes found by searching the PDFs. Used to seed demo history without LLM calls."""
    out: dict = {}
    for kind, values in record.items():
        if kind not in parsed:
            continue
        doc_id, pdf = parsed[kind]

        def evidence(*quotes):
            for quote in quotes:
                hit = locate(pdf, str(quote)) if quote else None
                if hit:
                    return {"document_id": doc_id, **hit}
            return None

        fields: dict = {}
        for spec in DOCUMENT_TYPES[kind]["fields"]:
            raw = values.get(spec.name)
            if raw in (None, ""):
                continue
            ev = evidence(raw)
            fields[spec.name] = {"value": normalize_value(spec.type, raw), "raw": raw,
                                 "confidence": 0.95 if ev else 0.55, "evidence": ev}
        if "line_items" in values:
            items = [{"cpt": li["cpt"], "description": li["description"], "units": li["units"],
                      "charge": normalize_value("money", li["charge"]),
                      "evidence": evidence(f"{li['cpt']} {li['description']}", li["cpt"])}
                     for li in values["line_items"]]
            fields["line_items"] = {"value": items, "confidence": 0.95}
        for list_name in ("diagnosis_codes", "approved_cpt_codes"):
            if list_name in values:
                fields[list_name] = {"value": [{"code": code, "evidence": evidence(code)} for code in values[list_name]],
                                     "confidence": 0.95}
        out[kind] = fields
    return out


def coding_reply(cases: list[dict], system: str, user: str) -> dict | None:
    """What a perfect coder would answer, from the enc_*/hcod_* answer keys. Used by the
    offline tests and to seed demo history without calling a model. None if not a coding call."""
    import json as _json

    from app.verticals.healthcare.prompts import CODING_EXTRACT_SYSTEM, CODING_SELECT_SYSTEM
    keyed = [c for c in cases if c.get("coding")]
    if system == CODING_EXTRACT_SYSTEM:
        text = " ".join(user.split())
        case = next((c for c in keyed if c["patient"] in text and all(d["quote"] in text for d in c["coding"]["diagnoses"])), None)
        if case is None:
            return {"visit": {"type": "procedure_only"}, "diagnoses": [], "procedures": []}
        k = case["coding"]
        return {"visit": k["visit"], "time_minutes": k["time_minutes"], "mdm": k["mdm"],
                "diagnoses": [{x: d[x] for x in ("condition", "search", "quote")} for d in k["diagnoses"]],
                "procedures": [{x: p[x] for x in ("service", "search", "quote", "units")} for p in k["procedures"]],
                "separate_em": k["separate_em"]}
    if system == CODING_SELECT_SYSTEM:
        payload = _json.loads(user)
        known = {(d["quote"], d.get("condition") or d.get("service")): d["code"] for c in keyed
                 for d in c["coding"]["diagnoses"] + c["coding"]["procedures"]}
        out = {}
        for kind in ("diagnoses", "procedures"):
            out[kind] = []
            for item in payload.get(kind, []):
                code = known.get((item["quote"], item["documented"]))
                offered = {c["code"] for c in item["candidates"]}
                out[kind].append({"index": item["index"], "code": code if code in offered else None,
                                  "reason": "answer key"})
        return out
    return None
