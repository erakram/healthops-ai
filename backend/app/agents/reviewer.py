"""Claims reviewer: rules first, then an LLM pass for what rules can't see.

The LLM may only add medium or low findings, and only with a quote that really appears
in the clinical note. Anything it can't back with a quote is dropped, so it can't
invent problems, and it can never be the reason a claim is high risk on its own.
"""

import json
from dataclasses import dataclass

from app.core.llm import LLM, LLMResult
from app.documents.pdf import ParsedPdf, locate
from app.verticals.healthcare.prompts import REVIEW_SYSTEM
from app.verticals.healthcare.rules import RuleFinding, run_rules

ALLOWED_AI_SEVERITIES = {"medium", "low"}


@dataclass
class ReviewResult:
    findings: list[RuleFinding]
    llm: LLMResult | None
    dropped_ai_findings: int = 0


def review_claim(record: dict, existing_claims: list[dict], llm: LLM | None,
                 note: tuple[int, ParsedPdf] | None, model: str | None = None) -> ReviewResult:
    findings = run_rules(record, existing_claims)
    if llm is None or note is None or "claim_form" not in record:
        return ReviewResult(findings, None)

    note_id, note_pdf = note
    claim = record["claim_form"]
    payload = {
        "billed_services": [{k: v for k, v in item.items() if k != "evidence"}
                            for item in (claim.get("line_items") or {}).get("value", [])],
        "diagnosis_codes": [d["code"] for d in (claim.get("diagnosis_codes") or {}).get("value", [])],
        "issues_already_found": [f.title for f in findings],
        "clinical_note": note_pdf.text[:12_000],
    }
    result = llm.complete_json(REVIEW_SYSTEM, json.dumps(payload, indent=1), model=model)

    dropped = 0
    for issue in result.data.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        hit = locate(note_pdf, issue.get("quote") or "")
        if not hit or issue.get("severity") not in ALLOWED_AI_SEVERITIES:
            dropped += 1
            continue
        findings.append(RuleFinding(code="AI-REVIEW", severity=issue["severity"], source="ai",
                                    title=str(issue.get("title") or "Possible documentation issue")[:300],
                                    detail=str(issue.get("detail") or ""),
                                    evidence=[{"document_id": note_id, **hit, "label": "Clinical note"}]))
    return ReviewResult(findings, result, dropped)
