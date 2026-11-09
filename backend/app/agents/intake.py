"""Intake Agent: turns the complaint as the patient said it into a structured note and a department.

The model extracts symptoms (each with the exact words it came from), duration, severity and a
department from a fixed list. Then code takes over:
  - a symptom whose quote isn't in the complaint is dropped,
  - red flags are decided by rules on the complaint and the extracted symptoms; the agent can add
    one (with a verified quote) but never clear one,
  - the department is overridden by age and pregnancy rules, and falls back to keyword rules
    when the agent's answer is missing or off-list.
If the model is unavailable, the desk still works on rules alone.
"""

import json
import logging

from app.core.llm import LLM, LLMError
from app.verticals.opd_india import red_flags
from app.verticals.opd_india.departments import DEPARTMENTS, department_by_rule, keyword_department, keyword_scores
from app.verticals.opd_india.prompts import INTAKE_SYSTEM
from app.verticals.opd_india.text import contains

logger = logging.getLogger(__name__)
SEVERITIES = {"mild", "moderate", "severe", "unknown"}


def intake_system() -> str:
    return INTAKE_SYSTEM.format(departments=", ".join(f"{k} ({v})" for k, v in DEPARTMENTS.items()))


def run_intake(llm: LLM | None, complaint: str, age: float | None, sex: str | None) -> dict:
    complaint = " ".join(complaint.split())
    reply, usage, error = {}, None, None
    if llm is not None:
        payload = {"complaint": complaint, "age_years": None if age is None else round(age, 1), "sex": sex}
        try:
            result = llm.complete_json(intake_system(), json.dumps(payload, ensure_ascii=False))
            reply = result.data if isinstance(result.data, dict) else {}
            usage = {"model": result.model, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                     "latency_ms": result.latency_ms}
        except LLMError as exc:  # includes rate limits: the desk can't wait a minute
            error = str(exc)
            logger.warning("Intake Agent unavailable, using rules only: %s", exc)
    else:
        error = "No LLM configured"

    symptoms, dropped = [], 0
    for s in reply.get("symptoms") or []:
        if isinstance(s, dict) and s.get("name") and contains(complaint, str(s.get("quote") or "")):
            symptoms.append({"name": str(s["name"]).strip(), "quote": str(s["quote"]).strip()})
        elif isinstance(s, dict):
            dropped += 1

    flags = red_flags.check(complaint, [s["name"] for s in symptoms], age)
    for sign in reply.get("red_flag_signs") or []:
        if isinstance(sign, dict) and contains(complaint, str(sign.get("quote") or "")) and not flags:
            flags.append({"id": "agent", "label": red_flags.FLAGS["agent"].label, "action": red_flags.FLAGS["agent"].action,
                          "matched": str(sign["quote"]), "sign": str(sign.get("sign") or "")})

    names = [s["name"] for s in symptoms]
    keyword = keyword_department(complaint, *names)
    agent_dept = reply.get("department") if reply.get("department") in DEPARTMENTS else None
    chosen, source, reason = agent_dept, "agent", str(reply.get("department_reason") or "").strip()
    if not chosen:
        chosen, source = keyword or "general_medicine", "keywords" if keyword else "default"
        reason = "Matched words in the complaint." if keyword else "Nothing specific: General Medicine sees and refers."
    rule = department_by_rule(age, sex, chosen, keyword_scores(complaint, *names))
    if rule:
        chosen, reason = rule
        source = "rule"

    severity = reply.get("severity") if reply.get("severity") in SEVERITIES else "unknown"
    return {
        "complaint": complaint,
        "language": reply.get("language") or None,
        "symptoms": symptoms,
        "dropped_symptoms": dropped,
        "duration": reply.get("duration") or None,
        "severity": severity,
        "summary_en": str(reply.get("summary_en") or "").strip() or None,
        "red_flags": flags,
        "department": chosen,
        "department_name": DEPARTMENTS[chosen],
        "department_source": source,  # agent | keywords | rule | default
        "department_reason": reason,
        "keyword_department": keyword,
        "agent_department": agent_dept,
        "agent": {"used": llm is not None and error is None, "error": error, "usage": usage},
    }
