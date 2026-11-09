"""Text helpers that work for English, Hinglish and Devanagari complaints."""

import re
import unicodedata

_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, punctuation and symbols to spaces, whitespace collapsed. Devanagari vowel
    signs are marks, not punctuation, so words keep their spelling."""
    text = unicodedata.normalize("NFC", text or "").lower()
    out = "".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in text)
    return _SPACE.sub(" ", out).strip()


def contains(haystack: str, quote: str) -> bool:
    """Whether the quote appears in the text, ignoring case, punctuation and spacing."""
    q, h = normalize(quote), normalize(haystack)
    if not q:
        return False
    return f" {q} " in f" {h} " or (len(q) >= 6 and q in h)
