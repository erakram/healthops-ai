"""Evaluate OPD intake with the real model against sample_data/opd_cases.json.

    python -m evals.run_opd_evals
    python -m evals.run_opd_evals --cases heel_no_keyword,acidity_burning

For each complaint (English, Hinglish, Devanagari): Intake Agent -> red-flag rules -> department rules.
Measures:
  - red-flag recall (must be 100%) and false emergencies on routine complaints
  - department accuracy: final, the agent's raw answer, and keywords alone (the baseline)
  - symptoms dropped because their quote wasn't in the complaint
  - Hindi output: the share of summaries and symptoms with a Devanagari version
  - tokens and time
Writes evals/reports/opd-<timestamp>.json.
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from app.agents.intake import run_intake
from app.config import BACKEND_ROOT, settings
from app.core.llm import RateLimited, make_llm
from app.verticals.opd_india.departments import department_by_rule, keyword_department, keyword_scores
from app.verticals.opd_india.red_flags import check

REPORTS = Path(__file__).resolve().parent / "reports"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", help="comma-separated case ids")
    args = parser.parse_args()
    cases = json.loads((BACKEND_ROOT / "sample_data" / "opd_cases.json").read_text())
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    llm = make_llm()
    if llm is None:
        raise SystemExit(f"Set {settings.llm_key_name} to run the evals against the real model.")

    rows, started = [], time.time()
    for case in cases:
        while True:
            try:
                note = run_intake(llm, case["complaint"], case["age"], case["sex"])
                if note["agent"]["error"] and "Rate limited" in note["agent"]["error"]:
                    raise RateLimited(note["agent"]["error"], 30)
                break
            except RateLimited as exc:
                print(f"  rate limited, waiting {exc.wait_seconds:.0f}s")
                time.sleep(exc.wait_seconds)
        flags = [f["id"] for f in note["red_flags"]]
        keyword = keyword_department(case["complaint"]) or "general_medicine"
        rule = department_by_rule(case["age"], case["sex"], keyword, keyword_scores(case["complaint"]))
        keyword_final = rule[0] if rule else keyword
        row = {
            "id": case["id"], "expected_department": case["department"], "department": note["department"],
            "agent_department": note["agent_department"], "keyword_department": keyword_final,
            "expected_red_flag": case["red_flag"], "red_flags": flags,
            "rules_only_red_flags": [f["id"] for f in check(case["complaint"], age=case["age"])],
            "symptoms": note["symptoms"], "dropped_symptoms": note["dropped_symptoms"], "language": note["language"],
            "summary_en": note["summary_en"], "summary_hi": note["summary_hi"],
            "symptoms_with_hindi": sum(1 for s in note["symptoms"] if s.get("name_hi")), "agent_error": note["agent"]["error"], "usage": note["agent"]["usage"],
        }
        rows.append(row)
        mark = ("ER " if flags else "MISS") if case["red_flag"] else ("ok " if row["department"] == case["department"] else "MISS")
        print(f"{mark} {case['id']:<22} {row['department']:<17} agent={row['agent_department']!s:<17} "
              f"keywords={keyword_final:<17} flags={flags}")

    flagged = [r for r in rows if r["expected_red_flag"]]
    routine = [r for r in rows if not r["expected_red_flag"]]

    def rate(hits, total):
        return round(hits / total, 3) if total else None

    metrics = {
        "cases": len(rows),
        "red_flag_recall": rate(sum(bool(r["red_flags"]) for r in flagged), len(flagged)),
        "red_flag_recall_rules_only": rate(sum(bool(r["rules_only_red_flags"]) for r in flagged), len(flagged)),
        "false_emergencies": sum(bool(r["red_flags"]) for r in routine),
        "department_accuracy": rate(sum(r["department"] == r["expected_department"] for r in routine), len(routine)),
        "agent_department_accuracy": rate(sum(r["agent_department"] == r["expected_department"] for r in routine), len(routine)),
        "keyword_department_accuracy": rate(sum(r["keyword_department"] == r["expected_department"] for r in routine), len(routine)),
        "dropped_symptoms": sum(r["dropped_symptoms"] for r in rows),
        "hindi_summary_rate": rate(sum(bool(r["summary_hi"]) for r in rows), len(rows)),
        "hindi_symptom_rate": rate(sum(r["symptoms_with_hindi"] for r in rows), sum(len(r["symptoms"]) for r in rows)),
        "agent_errors": sum(bool(r["agent_error"]) for r in rows),
        "input_tokens": sum((r["usage"] or {}).get("input_tokens", 0) for r in rows),
        "output_tokens": sum((r["usage"] or {}).get("output_tokens", 0) for r in rows),
        "avg_latency_ms": rate(sum((r["usage"] or {}).get("latency_ms", 0) for r in rows), len(rows)),
        "seconds": round(time.time() - started, 1),
        "model": settings.extraction_model,
    }
    print(json.dumps(metrics, indent=2))
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"opd-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"metrics": metrics, "cases": rows}, indent=1, ensure_ascii=False))
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
