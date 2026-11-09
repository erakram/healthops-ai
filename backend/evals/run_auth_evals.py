"""Evaluate the prior authorization agents with the real model against the pa_* answer keys.

    python -m evals.run_auth_evals
    python -m evals.run_auth_evals --cases pa_003,approved

Each case starts from its answer key (extraction is measured by run_evals.py). Then, for real:
  requirement + eligibility (rules) -> Clinical Criteria Agent -> Request Agent
  -> FHIR PAS request -> the payer's review -> FHIR PAS response, parsed
and, for cases with a physician's addendum: addendum -> re-check -> redraft -> resubmit.

Measures requirement and eligibility accuracy, recommended action, per-criterion accuracy,
missing documentation caught, citation verification and required evidence cited, whether
the payer's first decision matches the answer key, whether the addendum loop ends in
approval, and tokens and time. Writes evals/reports/auth-<timestamp>.json.
"""

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.agents.authorization import assess_documentation, check_requirement, draft_request
from app.agents.cited_writing import source_label
from app.config import settings
from app.core.llm import RateLimited, make_llm
from app.documents.pdf import parse_pdf
from app.verticals.healthcare import fhir_pas, payer
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.samples import FILE_KINDS, list_cases, truth_to_extracted

REPORTS = Path(__file__).resolve().parent / "reports"


class Metered:
    def __init__(self, llm):
        self.llm, self.tokens = llm, 0

    def complete_json(self, system, user, model=None):
        result = self.llm.complete_json(system, user, model)
        self.tokens += result.input_tokens + result.output_tokens
        return result


def _submit(case, record, docs, notes, assessment, policy, llm, model, attempt):
    form = case["record"]["service_order"]
    facts = {"request_number": f"PAR-E{case['case_id'][-3:]}", "order_number": form["order_number"],
             "patient": form["patient_name"], "member_id": form["member_id"], "payer": form["payer_name"],
             "provider": form["ordering_provider"], "urgency": form["urgency"],
             "scheduled_date": record["service_order"]["scheduled_date"]["value"],
             "services": check_requirement(record, date.today())["services"], "diagnosis_codes": form["diagnosis_codes"]}
    draft = draft_request(llm, facts, assessment, {**docs, **notes}, policy, model)
    citations = [{"source": c["source"], "quote": c["quote"]} for p in draft.paragraphs for c in p["citations"]]
    attached = [{"title": source_label(k), "text": pdf.text} for k, (_, pdf) in notes.items()]
    order = {"request_number": facts["request_number"], "attempt": attempt, "order_number": facts["order_number"],
             "urgency": facts["urgency"], "scheduled_date": facts["scheduled_date"],
             "patient": {"name": facts["patient"], "dob": None, "member_id": facts["member_id"]},
             "payer": {"name": facts["payer"], "id": payer.profile_for(facts["payer"]).id},
             "provider": {"name": facts["provider"], "npi": form["provider_npi"]}, "services": facts["services"],
             "diagnosis_codes": facts["diagnosis_codes"]}
    bundle = fhir_pas.build_request(order, citations, attached, "summary", datetime.now(timezone.utc))
    request = fhir_pas.read_request(bundle)
    review = payer.review_authorization(request, facts["payer"], attempt, date.today())
    response = fhir_pas.read_response(fhir_pas.build_response(request, review, datetime.now(timezone.utc)))
    return draft, response


def run_case(llm, case: dict, model: str) -> dict:
    started = time.perf_counter()
    meter = Metered(llm)
    key = case["pa"]
    docs = {FILE_KINDS[f]: (i, parse_pdf(f"{case['path']}/{f}")) for i, f in enumerate(case["files"])}
    record = truth_to_extracted(case["record"], docs)
    requirement = check_requirement(record, date.today())
    out = {"case_id": case["case_id"], "scenario": case["scenario"],
           "required": requirement["required"], "eligible": requirement["eligible"],
           "expected": {k: key.get(k) for k in ("required", "eligible", "action", "first_decision", "after_addendum")}}
    if not requirement["eligible"] or not requirement["required"]:
        out.update(action="ineligible" if not requirement["eligible"] else "none", tokens=0,
                   seconds=round(time.perf_counter() - started, 2))
        return out

    policy = get_policy(requirement["policy_id"])
    notes = {"clinical_note": docs["clinical_note"]}
    assessment = assess_documentation(meter, policy, requirement["services"], notes, model)
    out.update(action=assessment.action, policy_id=policy["id"],
               criteria={c["id"]: c["met"] for c in assessment.criteria}, expected_criteria=key["criteria"],
               model_overclaimed=[c["id"] for c in assessment.criteria if c["model_said_met"] and not c["met"]])
    draft, response = _submit(case, record, docs, notes, assessment.as_json(), policy, meter, model, 1)
    out.update(draft=draft.stats, removed=draft.removed, first_decision=response["decision"],
               payer_disposition=response["disposition"])

    if key.get("addendum"):
        notes["clinical_note_2"] = (99, parse_pdf(f"{case['path']}/extra/addendum_note.pdf"))
        again = assess_documentation(meter, policy, requirement["services"], notes, model)
        draft2, response2 = _submit(case, record, docs, notes, again.as_json(), policy, meter, model, 2)
        out.update(action_after_addendum=again.action, draft_after_addendum=draft2.stats,
                   decision_after_addendum=response2["decision"])
    out.update(tokens=meter.tokens, seconds=round(time.perf_counter() - started, 2))
    return out


def summarize(results: list[dict]) -> dict:
    def rate(hits, n):
        return round(hits / n, 3) if n else None

    checked = [r for r in results if "criteria" in r]
    criteria = [(r["criteria"].get(cid), want) for r in checked for cid, want in r["expected_criteria"].items()]
    gaps = [(got, want) for got, want in criteria if want is False]
    drafts = [r["draft"] for r in checked] + [r["draft_after_addendum"] for r in checked if "draft_after_addendum" in r]
    decided = [r for r in checked if r["expected"]["first_decision"]]
    loops = [r for r in checked if r["expected"].get("after_addendum")]
    return {
        "cases": len(results),
        "requirement_accuracy": rate(sum(r["required"] == r["expected"]["required"] for r in results), len(results)),
        "eligibility_accuracy": rate(sum(r["eligible"] == r["expected"]["eligible"] for r in results), len(results)),
        "action_accuracy": rate(sum(r["action"] == r["expected"]["action"] for r in results), len(results)),
        "criteria_accuracy": rate(sum(got == want for got, want in criteria), len(criteria)),
        "missing_documentation_caught": rate(sum(got is False for got, _ in gaps), len(gaps)),
        "model_overclaims_caught": sum(len(r["model_overclaimed"]) for r in checked),
        "citation_verification_rate": rate(sum(d["verified_citations"] for d in drafts), sum(d["citations"] for d in drafts)),
        "required_evidence_cited": rate(sum(d["evidence_cited"] for d in drafts), sum(d["evidence_required"] for d in drafts)),
        "paragraphs_removed": sum(d["removed_paragraphs"] for d in drafts),
        "first_decision_accuracy": rate(sum(r["first_decision"] == r["expected"]["first_decision"] for r in decided),
                                        len(decided)),
        "addendum_loop_approved": rate(sum(r.get("decision_after_addendum") == "approved" for r in loops), len(loops)),
        "avg_tokens_per_request": round(sum(r["tokens"] for r in checked) / len(checked)) if checked else 0,
        "avg_seconds_per_request": round(sum(r["seconds"] for r in checked) / len(checked), 1) if checked else 0,
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
    cases = list_cases("pa_")
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["case_id"] in wanted or c["scenario"] in wanted]

    results = []
    for i, case in enumerate(cases, 1):
        for _ in range(5):
            try:
                result = run_case(llm, case, model)
                break
            except RateLimited as exc:
                print(f"        rate limited, waiting {exc.wait_seconds:.0f}s")
                time.sleep(exc.wait_seconds)
        else:
            raise SystemExit("Rate limited 5 times in a row")
        results.append(result)
        ok = "ok " if result["action"] == result["expected"]["action"] else "BAD"
        line = f"[{i:>2}/{len(cases)}] {case['case_id']} {case['scenario']:<22} {ok} {result['action']:<22}"
        if "draft" in result:
            d = result["draft"]
            line += f" cites {d['verified_citations']}/{d['citations']} -> {result['first_decision']}"
        if "decision_after_addendum" in result:
            line += f", with addendum -> {result['decision_after_addendum']}"
        print(line)
        time.sleep(args.pause)

    summary = {**summarize(results), "model": model, "provider": settings.llm_provider}
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"auth-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"summary": summary, "cases": results}, indent=2))
    print("\nSummary")
    for k, v in summary.items():
        print(f"  {k:<34} {v}")
    print(f"\nReport: {path}")


if __name__ == "__main__":
    main()
