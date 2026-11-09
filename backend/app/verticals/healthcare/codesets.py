"""Code sets: ICD-10-CM (diagnoses) and a curated CPT list (procedures).

ICD-10-CM is the complete FY2026 list of billable codes published by CDC/NCHS (public
domain), stored compressed in codesets/. Search is BM25 over the descriptions with an
inverted index, so 74,000 codes are searched in milliseconds.

CPT is copyrighted by the American Medical Association, so the full set can't be
shipped. CPT_CODES below is a small curated list of the codes this demo uses, with our
own short descriptions, a chargemaster price, the most units billable per day (MUE) and
whether the code is an E/M visit. A real deployment would load the provider's licensed CPT
file behind the same functions.
"""

import gzip
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from app.core.retrieval import tokenize

ICD_FILE = Path(__file__).resolve().parent / "codesets" / "icd10cm-codes-2026.txt.gz"


def dotted(code: str) -> str:
    """ICD-10-CM codes are stored without the dot: 'M1711' -> 'M17.11'."""
    code = code.strip().upper().replace(".", "")
    return code if len(code) <= 3 else f"{code[:3]}.{code[3:]}"


def undotted(code: str) -> str:
    return code.strip().upper().replace(".", "")


@lru_cache
def icd10() -> dict[str, str]:
    """Billable ICD-10-CM codes (dotted) -> description."""
    codes = {}
    with gzip.open(ICD_FILE, "rt", encoding="utf-8") as f:
        for line in f:
            code, _, description = line.rstrip("\n").partition(" ")
            if code:
                codes[dotted(code)] = description.strip()
    return codes


def is_billable(code: str) -> bool:
    return dotted(code) in icd10()


def completions(code: str, limit: int = 8) -> list[tuple[str, str]]:
    """Billable codes that start with an incomplete code ('M17.1' -> M17.11, M17.12)."""
    prefix = dotted(code)
    return [(c, d) for c, d in icd10().items() if c.startswith(prefix) and c != prefix][:limit]


def siblings(code: str) -> list[tuple[str, str]]:
    """Codes in the same category (same first characters but the last)."""
    flat = undotted(code)
    if len(flat) <= 3:
        return []
    return [(c, d) for c, d in icd10().items() if undotted(c)[:-1] == flat[:-1] and undotted(c) != flat]


class _Index:
    """BM25 with an inverted index: only codes sharing a word with the query are scored."""

    def __init__(self, docs: dict[str, str], k1: float = 1.2, b: float = 0.6):
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self.lengths: dict[str, int] = {}
        for code, text in docs.items():
            counts = Counter(tokenize(text))
            self.lengths[code] = sum(counts.values()) or 1
            for term, tf in counts.items():
                self.postings[term].append((code, tf))
        n = len(docs)
        self.avg = sum(self.lengths.values()) / max(n, 1)
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        scores: Counter = Counter()
        for term in set(tokenize(query)):
            for code, tf in self.postings.get(term, ()):
                norm = self.k1 * (1 - self.b + self.b * self.lengths[code] / self.avg)
                scores[code] += self.idf[term] * tf * (self.k1 + 1) / (tf + norm)
        return [(code, round(score, 3)) for code, score in scores.most_common(k)]


@lru_cache
def _icd_index() -> _Index:
    return _Index(icd10())


_CODE_LIKE = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](\.?[0-9A-Z]{0,4})?$", re.IGNORECASE)


def search_icd10(query: str, k: int = 12) -> list[dict]:
    """Codes for a clinical phrase, or the code itself (and its completions) if one is typed."""
    query = query.strip()
    if _CODE_LIKE.match(query):
        exact = dotted(query)
        hits = ([{"code": exact, "description": icd10()[exact], "score": None}] if exact in icd10() else [])
        hits += [{"code": c, "description": d, "score": None} for c, d in completions(exact, k)]
        if hits:
            return hits[:k]
    return [{"code": c, "description": icd10()[c], "score": s} for c, s in _icd_index().search(query, k)]


# --- CPT (curated) ------------------------------------------------------------------------------

# code: (short description, chargemaster price, max units per day, is E/M)
CPT_CODES: dict[str, tuple[str, float, int, bool]] = {
    "99202": ("Office visit, new patient, straightforward", 110.00, 1, True),
    "99203": ("Office visit, new patient, low complexity", 170.00, 1, True),
    "99204": ("Office visit, new patient, moderate complexity", 255.00, 1, True),
    "99205": ("Office visit, new patient, high complexity", 335.00, 1, True),
    "99212": ("Office visit, established, straightforward", 90.00, 1, True),
    "99213": ("Office visit, established, low complexity", 145.00, 1, True),
    "99214": ("Office visit, established, moderate complexity", 215.00, 1, True),
    "99215": ("Office visit, established, high complexity", 295.00, 1, True),
    "36415": ("Routine venipuncture", 15.00, 2, False),
    "80053": ("Comprehensive metabolic panel", 62.50, 1, False),
    "85025": ("Complete blood count with differential", 38.00, 1, False),
    "81002": ("Urinalysis, dipstick, non-automated", 12.00, 2, False),
    "87880": ("Rapid strep test (group A streptococcus antigen)", 32.00, 2, False),
    "93000": ("Electrocardiogram, 12-lead, with interpretation and report", 78.00, 1, False),
    "71046": ("Chest X-ray, 2 views", 118.00, 1, False),
    "20610": ("Injection or aspiration, major joint (knee, shoulder, hip)", 205.00, 2, False),
    "20552": ("Trigger point injection, 1-2 muscles", 165.00, 1, False),
    "69210": ("Removal of impacted cerumen with instruments, one ear", 95.00, 1, False),
    "12001": ("Simple repair of superficial wound, 2.5 cm or less", 240.00, 1, False),
    "17110": ("Destruction of benign lesions (warts), up to 14", 160.00, 1, False),
    "97110": ("Therapeutic exercise, each 15 minutes", 82.00, 4, False),
    "64483": ("Transforaminal epidural injection, lumbar", 1250.00, 1, False),
    "72148": ("MRI lumbar spine without contrast", 1575.00, 1, False),
    "70553": ("MRI brain with and without contrast", 2380.00, 1, False),
    "93306": ("Echocardiogram, complete", 1095.00, 1, False),
    "29881": ("Knee arthroscopy with meniscectomy", 4720.00, 1, False),
}

# Minor procedures that bundle a same-day E/M unless it is separately identifiable (modifier 25).
EM_BUNDLED_WITH = {"20610", "20552", "69210", "12001", "17110"}

NEW_PATIENT = {1: "99202", 2: "99203", 3: "99204", 4: "99205"}
ESTABLISHED = {1: "99212", 2: "99213", 3: "99214", 4: "99215"}
# 2021 AMA office E/M time thresholds (minutes on the date of the encounter).
TIME_NEW = [(60, 4), (45, 3), (30, 2), (15, 1)]
TIME_ESTABLISHED = [(40, 4), (30, 3), (20, 2), (10, 1)]


def cpt_description(code: str) -> str | None:
    entry = CPT_CODES.get(code)
    return entry[0] if entry else None


@lru_cache
def _cpt_index() -> _Index:
    return _Index({code: f"{desc} {code}" for code, (desc, *_rest) in CPT_CODES.items() if not _rest[2]})


def search_cpt(query: str, k: int = 6) -> list[dict]:
    """Procedure codes (E/M visits are computed by rules, not searched)."""
    query = query.strip()
    if query in CPT_CODES:
        return [{"code": query, "description": CPT_CODES[query][0], "score": None}]
    return [{"code": c, "description": CPT_CODES[c][0], "score": s} for c, s in _cpt_index().search(query, k)]
