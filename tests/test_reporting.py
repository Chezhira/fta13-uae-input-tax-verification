from datetime import date
from types import SimpleNamespace

from pypdf import PdfReader

from fta13.reporting import build_pdf_report, evidence_strength_summary


def test_evidence_strength_distinguishes_hash_attestation_and_missing():
    from datetime import date
    from decimal import Decimal

    from fta13.engine import evaluate_supplier, evaluate_supply
    from fta13.models import Evidence, PersonType, Supplier, Supply

    as_of = date(2026, 10, 1)
    supplier = Supplier(
        "SUP-1",
        "Test",
        PersonType.LEGAL,
        verified_on=as_of,
        evidence=[
            Evidence("certificate_of_incorporation", "uploaded:cert.pdf", as_of, sha256="a" * 64),
            Evidence("representative_id", "user-confirmed:representative_id", as_of),
        ],
    )
    supply = Supply("INV-1", "SUP-1", as_of, Decimal("20000"), is_goods=True)
    supplier_outcome = evaluate_supplier(supplier, as_of=as_of)
    supply_outcome = evaluate_supply(supplier, supply)

    summary = evidence_strength_summary(
        supplier_outcome, supply_outcome, supplier.evidence, supply.evidence
    )
    assert summary["uploaded_and_hashed"] == 1
    assert summary["self_attested"] == 1
    assert summary["missing_document_requirements"] >= 1


def test_pdf_report_is_valid_and_handles_arabic():
    result = SimpleNamespace(
        clause_id="6.1",
        article="6(1)",
        verdict=SimpleNamespace(value="passed"),
        requirement="Supply threshold assessed.",
        detail="A. Reviewer on 2026-10-01: checked source page 1.",
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
    normalized_text = " ".join(text.split())
    assert "Ruleset version: fta13 0.1.0" in text
    assert "Generated on (UTC): 2026-08-21T12:00:00+00:00" in text
    assert "Reviewer (self-declared, not verified by this tool): A. Reviewer" in text
    assert "A. Reviewer on 2026-10-01: checked source page 1." in normalized_text


def test_pdf_article_6_exception_omits_supplier_gaps():
    supplier_gap = SimpleNamespace(
        clause_id="3.1.b.1",
        article="3(1)(b)(1)",
        verdict=SimpleNamespace(value="missing"),
        requirement="Supplier certificate should not appear in exception PDF.",
        detail="none on file",
    )
    supplier = SimpleNamespace(
        as_of=date(2026, 10, 1),
        supplier_id="SUP-EXEMPT",
        supply_id=None,
        results=[supplier_gap],
    )
    supply = SimpleNamespace(
        as_of=date(2026, 10, 1),
        supplier_id="SUP-EXEMPT",
        supply_id="INV-2400",
        results=[],
        assessment=SimpleNamespace(verification_required=False),
    )

    pdf = build_pdf_report(
        supplier_outcome=supplier,
        supply_outcome=supply,
        exception_available=True,
    )
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(__import__("io").BytesIO(pdf)).pages
    )

    assert "Article 6 exception available" in text
    assert "Supplier verification" not in text
    assert "Supplier certificate should not appear" not in text


def test_pdf_escapes_references_and_detail_markup():
    result = SimpleNamespace(
        clause_id="4.1.a",
        article="4(1)(a)",
        verdict=SimpleNamespace(value="satisfied"),
        requirement="A&B <terms> reviewed",
        detail="A&B <reviewer> confirmed the file",
    )
    supplier = SimpleNamespace(
        as_of=date(2026, 10, 1),
        supplier_id="A&B <TRADING>",
        supply_id=None,
        results=[result],
    )
    supply = SimpleNamespace(
        as_of=date(2026, 10, 1),
        supplier_id="A&B <TRADING>",
        supply_id="INV&A<1>",
        results=[result],
    )

    pdf = build_pdf_report(supplier_outcome=supplier, supply_outcome=supply)
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(__import__("io").BytesIO(pdf)).pages
    )
    normalized_text = " ".join(text.split())

    assert "A&B <TRADING>" in normalized_text
    assert "INV&A<1>" in normalized_text
    assert "A&B <reviewer> confirmed the file" in normalized_text
