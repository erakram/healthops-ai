"""BM25 keyword search over a small document collection.

Used to find the payer policy that applies to a denial. BM25 needs no embeddings API,
runs in microseconds on a few hundred documents, and its scores are explainable: a
reviewer can see which words matched. A vector index can replace it behind the same
`search` call when the policy library grows.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "is", "are", "be", "by", "with", "this",
         "that", "as", "at", "it", "if", "any", "not", "no", "may", "when", "from", "has", "have", "been"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Hit:
    id: str
    score: float
    matched: list[str]


class BM25:
    def __init__(self, documents: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = {doc_id: Counter(tokenize(text)) for doc_id, text in documents.items()}
        self.lengths = {doc_id: sum(c.values()) for doc_id, c in self.docs.items()}
        self.avg_length = sum(self.lengths.values()) / max(len(self.docs), 1)
        frequency = Counter(term for counts in self.docs.values() for term in counts)
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in frequency.items()}

    def search(self, query: str, k: int = 3) -> list[Hit]:
        terms = set(tokenize(query))
        hits = []
        for doc_id, counts in self.docs.items():
            score, matched = 0.0, []
            norm = self.k1 * (1 - self.b + self.b * self.lengths[doc_id] / self.avg_length)
            for term in terms:
                tf = counts.get(term, 0)
                if tf:
                    score += self.idf[term] * tf * (self.k1 + 1) / (tf + norm)
                    matched.append(term)
            if score > 0:
                hits.append(Hit(doc_id, round(score, 3), sorted(matched)))
        return sorted(hits, key=lambda h: -h.score)[:k]
