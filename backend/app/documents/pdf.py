"""Read PDFs with word positions, render pages, and ground quotes.

Grounding is how evidence works: the agent returns, for every value, the exact text it
read it from. We search for that text among the page's words. If it's there, the UI
can highlight the box; if it isn't, the value is marked unverified instead of trusted.
"""

import re
from dataclasses import dataclass

import pymupdf

_NORMALIZE = re.compile(r"[^a-z0-9]")


@dataclass
class Word:
    page: int  # 1-based
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class ParsedPdf:
    pages: int
    text: str  # with "--- page N ---" markers, which the agent sees
    words: list[Word]
    page_sizes: list[tuple[float, float]]


def parse_pdf(path: str) -> ParsedPdf:
    with pymupdf.open(path) as doc:
        parts, words, sizes = [], [], []
        for index, page in enumerate(doc, start=1):
            parts.append(f"--- page {index} ---\n{page.get_text('text').strip()}")
            sizes.append((page.rect.width, page.rect.height))
            for x0, y0, x1, y1, text, *_ in page.get_text("words"):
                words.append(Word(index, x0, y0, x1, y1, text))
        return ParsedPdf(pages=len(doc), text="\n\n".join(parts), words=words, page_sizes=sizes)


def render_page_png(path: str, page: int, zoom: float = 2.0) -> bytes:
    with pymupdf.open(path) as doc:
        if not 1 <= page <= len(doc):
            raise ValueError(f"Page {page} doesn't exist")
        return doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")


def _tokens(text: str) -> list[str]:
    return [t for t in (_NORMALIZE.sub("", part.lower()) for part in text.split()) if t]


def locate(parsed: ParsedPdf, quote: str) -> dict | None:
    """Find a quote among the words. Returns {page, bbox: [x0, y0, x1, y1] as fractions
    of the page size, text} so the frontend can draw the box at any zoom."""
    target = _tokens(quote or "")
    if not target:
        return None
    normalized = [(w, _NORMALIZE.sub("", w.text.lower())) for w in parsed.words]
    normalized = [(w, t) for w, t in normalized if t]
    n = len(target)
    for start in range(len(normalized) - n + 1):
        window = normalized[start:start + n]
        if window[0][0].page != window[-1][0].page:
            continue
        if all(t == target[i] for i, (_, t) in enumerate(window)):
            matched = [w for w, _ in window]
            page = matched[0].page
            width, height = parsed.page_sizes[page - 1]
            return {
                "page": page,
                "bbox": [round(min(w.x0 for w in matched) / width, 4), round(min(w.y0 for w in matched) / height, 4),
                         round(max(w.x1 for w in matched) / width, 4), round(max(w.y1 for w in matched) / height, 4)],
                "text": " ".join(w.text for w in matched),
            }
    return None
