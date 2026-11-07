"""Evaluate the denial agents with the real model against the den_* answer keys.

    python -m evals.run_denial_evals
    python -m evals.run_denial_evals --cases den_007,bundling_mod25

Extraction is already measured by run_evals.py, so each case starts from its answer key
(the same record a perfect Document Agent would produce). Then, for real:
  the mock payer adjudicates -> 835 text -> our parser -> Denial Triage Agent
  -> Appeal Agent (when triage recommends an appeal) -> the payer's appeal review

Measures:
  - 835 round trip: every claim balances and parses back to what the payer decided
  - triage: category, recommended action and policy retrieval against the answer key
  - criteria: each policy criterion judged met/not met correctly, with a verified quote
  - appeals: share of citations verified, paragraphs removed as unsupported, invented facts
  - outcomes: appeals the payer overturned where they should win, upheld where they shouldn't
  - tokens and latency
Writes evals/reports/denials-<timestamp>.json.
"""

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.agents.appeals import draft_appeal
from app.agents.denials import denial_groups, triage_denial
from app.config import settings
from app.core.llm import GroqLLM, RateLimited
from app.documents.pdf import parse_pdf
from app.revenue import payer_claim
from app.verticals.healthcare import payer
from app.verticals.healthcare.carc import CARCS
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.samples import FILE_KINDS, list_cases, truth_to_extracted
from app.verticals.healthcare.x12_835 import build_835, parse_835

REPORTS = Path(__file__).resolve().parent / "reports"


class Metered:
    """Wraps the LLM to add up tokens and time per case."""

    def __init__(self, llm):
        self.llm, self.tokens, self.calls = llm, 0, 0

    def complete_json(self, system, user, model=None):
        result = self.llm.complete_json(system, user, model)
        self.tokens += result.input_tokens + result.output_tokens
        self.calls += 1
        return result


def run_case(llm, case: dict, model: str) -> dict:
    started = time.perf_counter()
    meter = Metered(llm)
    key = case["denial"]
    docs = {FILE_KINDS[f]: (i, parse_pdf(f"{case['path']}/{f}")) for i, f in enumerate(case["files"])}
    record = truth_to_extracted(case["record"], docs)
    claim = SimpleNamespace(number=f"CLM-E{case['case_id'][-3:]}", extracted=record,
                            payer_name=case["record"]["claim_form"]["payer_name"],
                            service_date=record["claim_form"]["service_date"]["value"])
    today = date.today()
    request = payer_claim(claim)
    clp = payer.adjudicate(request, today)
    raw = build_835(payer.remittance(claim.payer_name, {"name": request["provider_name"], "npi": request["provider_npi"]},
                                     [clp], today, "EVAL"), "EVAL", "HEALTHOPS", 1, datetime.now(timezone.utc))
    parsed = parse_835(raw)
    got = parsed["claims"][0]
    out = {"case_id": case["case_id"], "scenario": case["scenario"],
           "x12_ok": not parsed["warnings"] and got["paid"] == clp["paid"] and got["status_code"] == clp["status_code"],
           "codes": sorted(f"{g['group']}-{g['code']}" for g in denial_groups(got)), "expected_codes": key["codes"]}
    groups = denial_groups(got)
    if not groups:
        out.update(seconds=round(time.perf_counter() - started, 2), tokens=0)
        return out

    group = groups[0]
    context = {"payer_name": claim.payer_name, "service_date": claim.service_date, "remit_date": today.isoformat(),
               "first_submitted_on": today.isoformat(), "claim_cpts": [l["cpt"] for l in got["lines"]]}
    triage = triage_denial(group, record, docs, context, meter, model)
    out.update(category=triage.category, action=triage.action, policy_id=triage.policy_id,
               expected=dict(category=key["category"], action=key["action"], policy_id=key["policy_id"]),
               criteria={c["id"]: c["met"] for c in triage.criteria if c["id"] in key.get("criteria", {})},
               expected_criteria=key.get("criteria", {}),
               model_overclaimed=[c["id"] for c in triage.criteria if c.get("model_said_met") and not c["met"]])

    should_win = key.get("appeal_should_win")
    # Also draft for cases that should lose, as if a biller appealed anyway: the appeal must
    # not win by inventing support the record doesn't have.
    if triage.action == "appeal" or should_win is not None:
        form = case["record"]["claim_form"]
        facts = {"claim_number": claim.number, "payer_claim_number": got["payer_control_number"],
                 "payer": claim.payer_name, "patient": form["patient_name"], "member_id": form["member_id"],
                 "provider": form["provider_name"], "service_date": claim.service_date, "remit_date": today.isoformat(),
                 "denial_code": f"{group['group']}-{group['code']}",
                 "denial_reason": CARCS[group["code"]].description, "remark_codes": group["remarks"],
                 "denied_amount": group["amount"], "billed_amount": got["charge"],
                 "services": [{"cpt": l["cpt"], "charge": l["charge"]} for l in group["lines"]],
                 "diagnosis_codes": form["diagnosis_codes"], "prior_auth_number": form.get("prior_auth_number")}
        draft = draft_appeal(meter, facts, triage.as_json(), docs, get_policy(triage.policy_id), model)
        citations = [c for p in draft.paragraphs for c in p["citations"]]
        decision = payer.review_appeal(triage.category, [l["cpt"] for l in group["lines"]], citations, record,
                                       claim.service_date, get_policy(triage.policy_id))
        out.update(appeal=draft.stats, removed=draft.removed, overturned=decision.overturned,
                   decision=decision.reason, should_win=should_win)
    if triage.action == "corrected_claim" and triage.fix:
        fixed = payer.adjudicate(payer_claim(claim, "7", {triage.fix["cpt"]: [triage.fix["add_modifier"]]}), today)
        out["corrected_paid"] = not denial_groups(fixed)
    out.update(seconds=round(time.perf_counter() - started, 2), tokens=meter.tokens, calls=meter.calls)
    return out


def summarize(results: list[dict]) -> dict:
    denied = [r for r in results if "category" in r]
    criteria = [(r["criteria"].get(cid), want) for r in denied for cid, want in r["expected_criteria"].items()]
    appeals = [r for r in denied if "appeal" in r]
    total_citations = sum(r["appeal"]["citations"] for r in appeals)
    winnable = [r for r in appeals if r.get("should_win") is True]
    losing = [r for r in appeals if r.get("should_win") is False]

    def rate(hits, n):
        return round(hits / n, 3) if n else None

    return {
        "cases": len(results),
        "x12_round_trip": rate(sum(r["x12_ok"] for r in results), len(results)),
        "payer_codes_as_planted": rate(sum(r["codes"] == sorted(r["expected_codes"]) for r in results), len(results)),
        "triage_category_accuracy": rate(sum(r["category"] == r["expected"]["category"] for r in denied), len(denied)),
        "triage_action_accuracy": rate(sum(r["action"] == r["expected"]["action"] for r in denied), len(denied)),
        "policy_retrieval_accuracy": rate(sum(r["policy_id"] == r["expected"]["policy_id"] for r in denied), len(denied)),
        "criteria_accuracy": rate(sum(got == want for got, want in criteria), len(criteria)),
        "model_overclaims_caught": sum(len(r["model_overclaimed"]) for r in denied),
        "appeals_drafted": len(appeals),
        "citation_verification_rate": rate(sum(r["appeal"]["verified_citations"] for r in appeals), total_citations),
        "paragraphs_removed": sum(r["appeal"]["removed_paragraphs"] for r in appeals),
        "invented_fact_paragraphs_caught": sum(r["appeal"]["invented_fact_paragraphs"] for r in appeals),
        "winnable_appeals_overturned": rate(sum(r["overturned"] for r in winnable), len(winnable)),
        "losing_appeals_overturned": rate(sum(r["overturned"] for r in losing), len(losing)),
        "corrected_claims_paid": rate(sum(r["corrected_paid"] for r in denied if "corrected_paid" in r),
                                      sum(1 for r in denied if "corrected_paid" in r)),
        "avg_tokens_per_denial": round(sum(r["tokens"] for r in denied) / len(denied)) if denied else 0,
        "avg_seconds_per_denial": round(sum(r["seconds"] for r in denied) / len(denied), 1) if denied else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", help="comma-separated case ids or scenarios")
    parser.add_argument("--model", help="override the model")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between cases (free-tier rate limits)")
    args = parser.parse_args()
    if not settings.groq_api_key:
        raise SystemExit("Set GROQ_API_KEY to run evals against the real model.")
    model = args.model or settings.review_model
    llm = GroqLLM(settings.groq_api_key, model, settings.request_timeout, settings.max_retries)
    cases = list_cases("den_")
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
        line = f"[{i:>2}/{len(cases)}] {case['case_id']} {case['scenario']:<26} codes {','.join(result['codes']) or '-':<8}"
        if "action" in result:
            ok = "ok " if result["action"] == result["expected"]["action"] else "BAD"
            line += f" {ok} {result['action']:<15}"
        if "appeal" in result:
            a = result["appeal"]
            line += (f" cites {a['verified_citations']}/{a['citations']} removed {a['removed_paragraphs']}"
                     f" -> {'overturned' if result['overturned'] else 'upheld'}")
        print(line)
        time.sleep(args.pause)

    summary = {**summarize(results), "model": model}
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"denials-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"summary": summary, "cases": results}, indent=2))
    print("\nSummary")
    for k, v in summary.items():
        print(f"  {k:<36} {v}")
    print(f"\nReport: {path}")


if __name__ == "__main__":
    main()
