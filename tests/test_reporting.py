from datetime import date
from types import SimpleNamespace

from pypdf import PdfReader

from fta13.reporting import build_pdf_report


def test_pdf_report_is_valid_and_handles_arabic():
    result = SimpleNamespace(
        clause_id="6.1",
        article="6(1)",
        verdict=SimpleNamespace(value="passed"),
        requirement="Supply threshold assessed.",
    )
    supplier = SimpleNamespace(
        as_of=date(2026, 10, 1),
        supplier_id="SUP-1",
        supply_id=None,
        results=[result],
    )
    supply = SimpleNamespace(
        as_of=date(2026, 10, 1),
        supplier_id="SUP-1",
        supply_id="INV-1",
        results=[result],
    )
    pdf = build_pdf_report(
        supplier_outcome=supplier,
        supply_outcome=supply,
        extracted_document={
            "supplier_name_ar": {"original": "شركة المثال"},
            "supplier_name_en": {"original": "Example LLC"},
            "invoice_number": {"original": "INV-1"},
        },
        ruleset_label="fta13 0.1.0",
        generated_on_utc="2026-08-21T12:00:00+00:00",
        reviewer="A. Reviewer",
    )
    assert pdf.startswith(b"%PDF-")
    reader = PdfReader(__import__("io").BytesIO(pdf))
    assert len(reader.pages) >= 1
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Ruleset version: fta13 0.1.0" in text
    assert "Generated on (UTC): 2026-08-21T12:00:00+00:00" in text
    assert "Reviewer (self-declared, not verified by this tool): A. Reviewer" in text
