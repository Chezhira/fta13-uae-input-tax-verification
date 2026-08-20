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
    )
    assert pdf.startswith(b"%PDF-")
    assert len(PdfReader(__import__("io").BytesIO(pdf)).pages) >= 1
