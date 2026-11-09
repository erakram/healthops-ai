"""Evaluate the Coding Agent with the real model against the enc_* answer keys.

    python -m evals.run_coding_evals
    python -m evals.run_coding_evals --cases enc_003,knee_injection_mod25

For each encounter note: read (model) -> ICD-10-CM / CPT search -> select (model) -> E/M
rules -> coding checks. Measures:
  - ICD-10 precision and recall, and whether the primary diagnosis is right
  - CPT precision and recall (code + modifiers + units), and exact E/M level
  - MDM element accuracy (problems, data, risk levels against the answer key)
  - search recall: the right code was among the candidates the model was shown
  - off-list answers rejected, quotes grounded in the note, coding checks raised
  - tokens and time
Writes evals/reports/coding-<timestamp>.json.
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from app.agents.coding import code_encounter
from app.config import settings
from app.core.llm import RateLimited, make_llm
from app.documents.pdf import parse_pdf
from app.verticals.healthcare.samples import list_cases

REPORTS = Path(__file__).resolve().parent / "reports"


class Metered:
    def __init__(self, llm):
        self.llm, self.tokens = llm, 0

    def complete_json(self, system, user, model=None):
        result = self.llm.complete_json(system, user, model)
        self.tokens += result.input_tokens + result.output_tokens
        return result


def _line(p: dict) -> str:
    return p["code"] + "".join(f"-{m}" for m in sorted(p.get("modifiers") or [])) + (f"x{p['units']}" if p.get("units", 1) > 1 else "")


def run_case(llm, case: dict, model: str) -> dict:
    started = time.perf_counter()
    meter = Metered(llm)
    key = case["coding"]
    result = code_encounter(meter, (1, parse_pdf(f"{case['path']}/clinical_note.pdf")), model)
    got_icd = [d["code"] for d in result.diagnoses]
    got_cpt = [_line(p) for p in result.procedures]
    want_icd, want_cpt = key["expected"]["icd"], [_line(p) for p in key["expected"]["cpt"]]
    searched = {code for codes in result.stats["searched"] for code in codes}
    mdm_got = {k: str((result.documented["mdm"].get(k) or {}).get("level") or "minimal").lower() for k in ("problems", "data", "risk")}
    return {
        "case_id": case["case_id"], "scenario": case["scenario"],
        "icd": got_icd, "expected_icd": want_icd, "cpt": got_cpt, "expected_cpt": want_cpt,
        "em": (result.em or {}).get("code"), "expected_em": key["expected"]["em"],
        "mdm": mdm_got, "expected_mdm": {k: v["level"] for k, v in key["mdm"].items()},
        "search_hits": sum(1 for c in want_icd + [l["code"] for l in key["expected"]["cpt"] if not l["code"].startswith("992")]
                           if c in searched),
        "search_targets": len(want_icd) + sum(1 for l in key["expected"]["cpt"] if not l["code"].startswith("992")),
        "rejected": result.stats["rejected_choices"], "grounded": result.stats["grounded"], "quotes": result.stats["quotes"],
        "edits": [e["code"] for e in result.edits], "tokens": meter.tokens, "seconds": round(time.perf_counter() - started, 2),
    }


def summarize(results: list[dict]) -> dict:
    def rate(hits, n):
        return round(hits / n, 3) if n else None

    def pr(got_key, want_key):
        tp = sum(len(set(r[got_key]) & set(r[want_key])) for r in results)
        return rate(tp, sum(len(r[got_key]) for r in results)), rate(tp, sum(len(r[want_key]) for r in results))

    icd_p, icd_r = pr("icd", "expected_icd")
    cpt_p, cpt_r = pr("cpt", "expected_cpt")
    em_cases = [r for r in results if r["expected_em"] or r["em"]]
    mdm = [(r["mdm"][k], v) for r in results if r["expected_em"] for k, v in r["expected_mdm"].items()]
    return {
        "cases": len(results),
        "icd_precision": icd_p, "icd_recall": icd_r,
        "primary_diagnosis_accuracy": rate(sum(bool(r["icd"]) and r["icd"][0] == r["expected_icd"][0] for r in results), len(results)),
        "cpt_precision": cpt_p, "cpt_recall": cpt_r,
        "em_level_accuracy": rate(sum(r["em"] == r["expected_em"] for r in em_cases), len(em_cases)),
        "mdm_element_accuracy": rate(sum(got == want for got, want in mdm), len(mdm)),
        "search_recall": rate(sum(r["search_hits"] for r in results), sum(r["search_targets"] for r in results)),
        "exact_match_encounters": rate(sum(r["icd"] == r["expected_icd"] and sorted(r["cpt"]) == sorted(r["expected_cpt"])
                                           for r in results), len(results)),
        "off_list_answers_rejected": sum(r["rejected"] for r in results),
        "grounding_rate": rate(sum(r["grounded"] for r in results), sum(r["quotes"] for r in results)),
        "coding_checks_raised": sum(len(r["edits"]) for r in results),
        "avg_tokens_per_encounter": round(sum(r["tokens"] for r in results) / len(results)) if results else 0,
        "avg_seconds_per_encounter": round(sum(r["seconds"] for r in results) / len(results), 1) if results else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", help="comma-separated case ids or scenarios")
    parser.add_argument("--model", help="override the model")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between cases (free-tier rate limits)")
    args = parser.parse_args()
    if not settings.llm_api_key:
        raise SystemExit(f"Set {settings.llm_key_name} to run evals against the real model.")
    model = args.model or settings.review_model
    llm = make_llm(model)
    cases = list_cases("enc_")
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["case_id"] in wanted or c["scenario"] in wanted]
    results, errors = [], []
    for i, case in enumerate(cases, 1):
        for _ in range(5):
            try:
                result = run_case(llm, case, model)
                break
            except RateLimited as exc:
                print(f"        rate limited, waiting {exc.wait_seconds:.0f}s")
                time.sleep(exc.wait_seconds)
            except Exception as exc:  # a crash is a result too
                result = {"case_id": case["case_id"], "error": f"{type(exc).__name__}: {exc}"}
                break
        else:
            raise SystemExit("Rate limited 5 times in a row")
        if "error" in result:
            errors.append(result)
            print(f"[{i:>2}/{len(cases)}] {case['case_id']} ERROR {result['error'][:100]}")
        else:
            results.append(result)
            ok = "ok " if result["icd"] == result["expected_icd"] and sorted(result["cpt"]) == sorted(result["expected_cpt"]) else "BAD"
            print(f"[{i:>2}/{len(cases)}] {case['case_id']} {case['scenario']:<22} {ok} ICD {','.join(result['icd']) or '-':<16} "
                  f"CPT {','.join(result['cpt']) or '-'}" + ("" if ok == "ok " else
                                                             f"   expected {','.join(result['expected_icd'])} / {','.join(result['expected_cpt'])}"))
        time.sleep(args.pause)
    summary = {**summarize(results), "errors": len(errors), "model": model, "provider": settings.llm_provider}
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"coding-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"summary": summary, "cases": results, "errors": errors}, indent=2))
    print("\nSummary")
    for k, v in summary.items():
        print(f"  {k:<30} {v}")
    print(f"\nReport: {path}")


if __name__ == "__main__":
    main()
