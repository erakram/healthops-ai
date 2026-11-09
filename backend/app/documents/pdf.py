"""Read PDFs with word positions, render pages, and ground quotes.

Grounding is how evidence works: the agent returns, for every value, the exact text it
read it from. We search for that text among the page's words. If it's there, the UI
can highlight the box; if it isn't, the value is marked unverified instead of trusted.
"""

import re
from dataclasses import dataclass, field

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
    ocr_pages: list[int] = field(default_factory=list)  # scanned pages read with OCR
    needs_ocr: list[int] = field(default_factory=list)  # scanned pages that couldn't be read here


MIN_TEXT_CHARS = 20  # a page with less text than this but an image on it is treated as a scan


def _ocr_words(page, index: int) -> list[Word]:
    from app.documents import ocr
    png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
    w, h = page.rect.width, page.rect.height
    return [Word(index, o["x0"] * w, o["y0"] * h, o["x1"] * w, o["y1"] * h, o["text"]) for o in ocr.ocr_png(png)]


def _lines(words: list[Word]) -> str:
    """Reading order for OCR words: group into lines by vertical position, left to right."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        if lines and abs(lines[-1][0].y0 - word.y0) < (word.y1 - word.y0) * 0.6:
            lines[-1].append(word)
        else:
            lines.append([word])
    return "\n".join(" ".join(w.text for w in sorted(line, key=lambda w: w.x0)) for line in lines)


def parse_pdf(path: str) -> ParsedPdf:
    from app.documents import ocr
    with pymupdf.open(path) as doc:
        parts, words, sizes, ocr_pages, needs_ocr = [], [], [], [], []
        for index, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            sizes.append((page.rect.width, page.rect.height))
            page_words = [Word(index, x0, y0, x1, y1, t) for x0, y0, x1, y1, t, *_ in page.get_text("words")]
            if len(text) < MIN_TEXT_CHARS and page.get_images():
                if ocr.available():
                    page_words = _ocr_words(page, index)
                    text = _lines(page_words)
                    ocr_pages.append(index)
                else:
                    needs_ocr.append(index)
            parts.append(f"--- page {index} ---\n{text}")
            words.extend(page_words)
        return ParsedPdf(pages=len(doc), text="\n\n".join(parts), words=words, page_sizes=sizes,
                         ocr_pages=ocr_pages, needs_ocr=needs_ocr)


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
