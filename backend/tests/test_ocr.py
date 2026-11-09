"""Scanned PDFs: read with on-device OCR (macOS Vision), or flagged when OCR isn't available."""

import pymupdf
import pytest

from app.agents.extraction import extract_document
from app.documents import ocr
from app.documents import pdf as pdf_module
from app.verticals.healthcare.samples import list_cases
from tests.fake_llm import FakeLLM

CASE = next(c for c in list_cases("case_") if c["scenario"] == "clean")


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    """The sample claim form, printed and scanned: an image-only PDF."""
    src = pymupdf.open(f"{CASE['path']}/claim_form.pdf")
    out = pymupdf.open()
    page = out.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.insert_image(page.rect, pixmap=src[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)))
    path = tmp_path_factory.mktemp("scan") / "scanned_claim.pdf"
    out.save(path)
    return str(path)


@pytest.mark.skipif(not ocr.available(), reason="on-device OCR needs macOS and swiftc")
def test_scanned_claim_is_read_and_evidence_still_points_at_the_page(scanned):
    parsed = pdf_module.parse_pdf(scanned)
    assert parsed.ocr_pages == [1] and not parsed.needs_ocr
    record = CASE["record"]["claim_form"]
    for value in (record["member_id"], record["claim_number"], record["total_charge"]):
        hit = pdf_module.locate(parsed, value)
        assert hit and 0 <= hit["bbox"][0] < hit["bbox"][2] <= 1, value
    result = extract_document(FakeLLM(), parsed, 1)  # the Document Agent works on the OCR text
    assert result.document_type == "claim_form" and result.fields["member_id"]["evidence"]


def test_without_ocr_scans_are_flagged_not_read_as_empty(scanned, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    parsed = pdf_module.parse_pdf(scanned)
    assert parsed.needs_ocr == [1] and not parsed.ocr_pages
