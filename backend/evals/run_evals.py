"""Evaluate the real agents against the answer keys of the sample cases.

    python -m evals.run_evals                 # all case_* packets
    python -m evals.run_evals --limit 6       # a quick subset
    python -m evals.run_evals --cases case_017,note_mismatch
    python -m evals.run_evals --model llama-3.3-70b-versatile

Measures, per run:
  - document classification accuracy
  - field accuracy (values compared after normalisation: dates, money, names, IDs)
  - grounding rate (share of extracted values found on the page)
  - finding precision and recall against the planted problems
  - whether the AI reviewer caught the problems only it can see, and its false alarms
  - tokens and latency

Writes evals/reports/<timestamp>.json and prints a summary table.
This calls the LLM provider for real; it needs ANTHROPIC_API_KEY (Claude) or GROQ_API_KEY.
"""

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app.agents.extraction import extract_document
from app.agents.reviewer import review_claim
from app.config import settings
from app.core.llm import RateLimited, make_llm
from app.documents.pdf import parse_pdf
from app.verticals.healthcare.rules import RuleFinding
from app.verticals.healthcare.samples import FILE_KINDS, list_cases
from app.verticals.healthcare.schema import (DOCUMENT_TYPES, normalize_id, normalize_name, parse_date, parse_money)

REPORTS = Path(__file__).resolve().parent / "reports"


def same(spec_type: str, name: str, expected, actual) -> bool:
    if actual is None:
        return False
    if spec_type == "date":
        return parse_date(expected) is not None and parse_date(expected) == parse_date(actual)
    if spec_type == "money":
        return parse_money(expected) == parse_money(actual)
    if "name" in name and name not in ("payer_name", "provider_name"):
        return normalize_name(expected) == normalize_name(actual)
    if name.endswith(("_id", "_number", "_npi")):
        return normalize_id(expected) == normalize_id(actual)
    return str(expected).strip().lower() == str(actual).strip().lower()


def run_case(llm, case: dict, model: str | None) -> dict:
    started = time.perf_counter()
    record, results, docs = {}, [], {}
    suspicious = []
    for i, filename in enumerate(case["files"]):
        pdf = parse_pdf(f"{case['path']}/{filename}")
        result = extract_document(llm, pdf, i, model=model)
        results.append((filename, result))
        docs[result.document_type] = (i, pdf)
        if result.document_type in DOCUMENT_TYPES and result.document_type not in record:
            record[result.document_type] = result.fields
        if result.suspicious_text:
            suspicious.append(filename)

    review = review_claim(record, [], llm, docs.get("clinical_note"), model=model)
    findings: list[RuleFinding] = review.findings + [RuleFinding("PROMPT-INJECTION", "critical", "", "") for _ in suspicious]

    # --- score ---
    classification = [(FILE_KINDS[f], r.document_type) for f, r in results]
    field_scores, misses = [], []
    for kind, values in case["record"].items():
        specs = {s.name: s for s in DOCUMENT_TYPES[kind]["fields"]}
        got = record.get(kind, {})
        for name, expected in values.items():
            if expected in (None, "") or name not in specs:
                continue
            actual = (got.get(name) or {}).get("raw")
            ok = same(specs[name].type, name, expected, actual)
            field_scores.append(ok)
            if not ok:
                misses.append(f"{kind}.{name}: expected {expected!r}, got {actual!r}")
        for list_name, key in (("line_items", "cpt"), ("diagnosis_codes", None), ("approved_cpt_codes", None)):
            if list_name in values:
                expected = sorted(x[key] if key else x for x in values[list_name])
                actual = sorted((x.get(key) if key else x.get("code")) for x in (got.get(list_name) or {}).get("value", []))
                field_scores.append(expected == actual)
                if expected != actual:
                    misses.append(f"{kind}.{list_name}: expected {expected}, got {actual}")

    extracted_values = [f for fields in record.values() for f in fields.values() if "evidence" in f]
    grounded = sum(1 for f in extracted_values if f.get("evidence"))
    rule_codes = {f.code for f in findings if f.source == "rule"}
    expected_codes = set(case["expected_findings"])
    ai_findings = [f for f in findings if f.source == "ai"]
    runs = [r.llm for _, r in results if r.llm] + ([review.llm] if review.llm else [])
    return {
        "case_id": case["case_id"], "scenario": case["scenario"],
        "classification_correct": sum(a == b for a, b in classification), "documents": len(classification),
        "fields_correct": sum(field_scores), "fields_total": len(field_scores), "field_misses": misses,
        "grounded": grounded, "extracted": len(extracted_values),
        "expected_findings": sorted(expected_codes), "found_findings": sorted(rule_codes),
        "true_positive": len(expected_codes & rule_codes), "false_positive": sorted(rule_codes - expected_codes),
        "false_negative": sorted(expected_codes - rule_codes),
        "expects_ai_review": case["expects_ai_review"], "ai_findings": [f.title for f in ai_findings],
        "tokens": sum(r.input_tokens + r.output_tokens for r in runs), "llm_calls": len(runs),
        "seconds": round(time.perf_counter() - started, 1),
    }


def summarize(results: list[dict]) -> dict:
    tp = sum(r["true_positive"] for r in results)
    fp = sum(len(r["false_positive"]) for r in results)
    fn = sum(len(r["false_negative"]) for r in results)
    ai_cases = [r for r in results if r["expects_ai_review"]]
    other = [r for r in results if not r["expects_ai_review"]]
    return {
        "cases": len(results),
        "classification_accuracy": round(sum(r["classification_correct"] for r in results) / max(sum(r["documents"] for r in results), 1), 3),
        "field_accuracy": round(sum(r["fields_correct"] for r in results) / max(sum(r["fields_total"] for r in results), 1), 3),
        "grounding_rate": round(sum(r["grounded"] for r in results) / max(sum(r["extracted"] for r in results), 1), 3),
        "finding_precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "finding_recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "ai_review_recall": round(sum(1 for r in ai_cases if r["ai_findings"]) / len(ai_cases), 3) if ai_cases else None,
        "ai_review_false_alarm_cases": sum(1 for r in other if r["ai_findings"]),
        "avg_tokens_per_claim": round(sum(r["tokens"] for r in results) / max(len(results), 1)),
        "avg_seconds_per_claim": round(sum(r["seconds"] for r in results) / max(len(results), 1), 1),
        "missed_by_code": dict(Counter(code for r in results for code in r["false_negative"])),
        "false_alarms_by_code": dict(Counter(code for r in results for code in r["false_positive"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="only the first N cases")
    parser.add_argument("--cases", help="comma-separated case ids or scenarios, e.g. case_017,note_mismatch")
    parser.add_argument("--model", help="override both extraction and review model")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between cases (free-tier rate limits)")
    args = parser.parse_args()
    if not settings.llm_api_key:
        raise SystemExit(f"Set {settings.llm_key_name} to run evals against the real model.")

    model = args.model or settings.extraction_model
    llm = make_llm(model)
    cases = list_cases("case_")
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["case_id"] in wanted or c["scenario"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    results = []
    for i, case in enumerate(cases, 1):
        for _ in range(5):
            try:
                result = run_case(llm, case, model)
                break
            except RateLimited as exc:  # free tiers have small per-minute budgets: wait and redo the case
                print(f"        rate limited, waiting {exc.wait_seconds:.0f}s")
                time.sleep(exc.wait_seconds)
            except Exception as exc:  # keep going; a crash is a result too
                result = {"case_id": case["case_id"], "scenario": case["scenario"], "error": str(exc)}
                break
        else:
            result = {"case_id": case["case_id"], "scenario": case["scenario"], "error": "rate limited 5 times"}
        results.append(result)
        status = "ERROR " + result["error"][:80] if "error" in result else (
            f"fields {result['fields_correct']}/{result['fields_total']}  "
            f"missed {result['false_negative'] or '-'}  extra {result['false_positive'] or '-'}"
            + (f"  ai: {len(result['ai_findings'])}" if result["ai_findings"] or result["expects_ai_review"] else ""))
        print(f"[{i:>2}/{len(cases)}] {case['case_id']} {case['scenario']:<20} {status}")
        time.sleep(args.pause)

    ok = [r for r in results if "error" not in r]
    summary = {**summarize(ok), "errors": len(results) - len(ok), "model": model, "provider": settings.llm_provider}
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"summary": summary, "cases": results}, indent=2))
    print("\nSummary")
    for key, value in summary.items():
        print(f"  {key:<30} {value}")
    print(f"\nReport: {path}")


if __name__ == "__main__":
    main()
