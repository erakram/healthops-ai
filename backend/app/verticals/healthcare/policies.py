"""The payer policy library: coverage criteria the denial agents check documentation against.

Policies are synthetic and shared by the four synthetic payers. Each criterion has a
plain-language text (what the agents and people read) and a pattern, which is what the
mock payer's reviewer uses to decide an appeal deterministically, so outcomes are
reproducible and can be scored in evals.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.core.retrieval import BM25, Hit

POLICY_FILE = Path(__file__).resolve().parent / "policies.json"


@lru_cache
def all_policies() -> dict[str, dict]:
    return {p["id"]: p for p in json.loads(POLICY_FILE.read_text())}


def get_policy(policy_id: str | None) -> dict | None:
    return all_policies().get(policy_id or "")


@lru_cache
def _index() -> BM25:
    docs = {}
    for p in all_policies().values():
        criteria = " ".join(c["text"] for c in p["criteria"])
        codes = " ".join(p["cpt_codes"]) + " " + " ".join(f"reason {c}" for c in p["carc_codes"])
        docs[p["id"]] = f"{p['title']} {p['title']} {p['body']} {criteria} {codes}"
    return BM25(docs)


def search_policies(query: str, k: int = 3) -> list[Hit]:
    return _index().search(query, k)


def criterion_satisfied(criterion: dict, text: str) -> bool:
    """Whether a piece of documentation text meets a criterion's pattern (and minimum number)."""
    for match in re.finditer(criterion["pattern"], text, flags=re.IGNORECASE):
        minimum = criterion.get("min_number")
        if minimum is None:
            return True
        try:
            if float(match.group(1)) >= minimum:
                return True
        except (IndexError, TypeError, ValueError):
            continue
    return False


def bundled_with(cpt: str, other_cpts: list[str]) -> str | None:
    """If `cpt` is bundled into another billed procedure, return that procedure's code."""
    for policy in all_policies().values():
        for column_one, column_two in policy.get("bundling_pairs", []):
            if column_two == cpt and column_one in other_cpts:
                return column_one
    return None
