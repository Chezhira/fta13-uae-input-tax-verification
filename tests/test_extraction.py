from datetime import date
from decimal import Decimal

import pytest

from fta13.extraction import (
    DocumentExtraction,
    ExtractedValue,
    SourceReference,
    batch_identity_conflicts,
    batch_identity_rows,
    merge_extractions,
    sha256_bytes,
    validate_upload,
)


def value(original: str, normalized: str, confidence: float, language="en"):
    return ExtractedValue(
        original=original,
        normalized=normalized,
        confidence=confidence,
        sources=[SourceReference(page=1, quote=original, language=language)],
    )


def test_arabic_and_english_values_are_preserved():
    extraction = DocumentExtraction(
        detected_languages=["ar", "en", "ar"],
        supplier_name_ar=value("شركة المثال", "شركة المثال", 0.98, "ar"),
        supplier_name_en=value("Example LLC", "Example LLC", 0.97),
        invoice_date=value("١ أكتوبر ٢٠٢٦", "2026-10-01", 0.91, "ar"),
        consideration_ex_vat=value("١٢٬٥٠٠٫٠٠", "12500.00", 0.90, "ar"),
    )
    assert extraction.detected_languages == ["ar", "en"]
    assert extraction.invoice_date_value() == date(2026, 10, 1)
    assert extraction.decimal_value() == Decimal("12500.00")
    assert extraction.supplier_name_ar.original == "شركة المثال"


def test_merge_keeps_most_confident_supported_value():
    first = DocumentExtraction(
        invoice_number=value("INV-100?", "INV-100", 0.55),
        detected_languages=["en"],
    )
    second = DocumentExtraction(
        invoice_number=value("INV-100", "INV-100", 0.99),
        detected_languages=["ar", "en"],
        evidence_kinds=["origin_document"],
    )
    merged = merge_extractions([first, second])
    assert merged.invoice_number.normalized == "INV-100"
    assert merged.detected_languages == ["en", "ar"]
    assert merged.evidence_kinds == ["origin_document"]


def test_upload_validation_and_hashing():
    content = b"%PDF-1.7 example"
    validate_upload("invoice.pdf", "application/pdf", content)
    assert len(sha256_bytes(content)) == 64
    with pytest.raises(ValueError):
        validate_upload("invoice.exe", "application/pdf", content)
    with pytest.raises(ValueError):
        validate_upload("invoice.txt", "text/plain", b"x")


def test_source_rows_expose_page_quote_and_language():
    extraction = DocumentExtraction(
        supplier_name_ar=ExtractedValue(
            original="شركة النور",
            normalized="شركة النور",
            confidence=0.96,
            sources=[
                SourceReference(
                    page=2,
                    quote="اسم المورد: شركة النور",
                    language="ar",
                )
            ],
        )
    )

    assert extraction.source_rows() == [
        {
            "Field": "Supplier Name Ar",
            "Page": 2,
            "Language": "ar",
            "Source quote": "اسم المورد: شركة النور",
        }
    ]


def test_missing_and_invalid_values_remain_unset():
    extraction = DocumentExtraction(
        invoice_date=ExtractedValue(normalized="not-a-date"),
        consideration_ex_vat=ExtractedValue(normalized="not-an-amount"),
    )

    assert extraction.invoice_date_value() is None
    assert extraction.decimal_value() is None
    assert extraction.review_rows() == []
    assert extraction.source_rows() == []
    assert merge_extractions([]) == DocumentExtraction()


def test_upload_validation_rejects_empty_and_oversized_documents():
    with pytest.raises(ValueError, match="empty"):
        validate_upload("invoice.pdf", "application/pdf", b"")
    with pytest.raises(ValueError, match="5 MB"):
        validate_upload(
            "invoice.pdf",
            "application/pdf",
            b"x" * (5 * 1024 * 1024 + 1),
        )


def test_batch_identity_rows_compare_each_document():
    first = DocumentExtraction(
        document_type="commercial_licence",
        supplier_name_en=value("Al Noor LLC", "Al Noor LLC", 0.98),
        trn=value("100123456700003", "100123456700003", 0.99),
    )
    second = DocumentExtraction(
        document_type="tax_invoice",
        supplier_name_en=value("AL NOOR LLC", "AL NOOR LLC", 0.97),
        trn=value("١٠٠١٢٣٤٥٦٧٠٠٠٠٣", "100123456700003", 0.99, "ar"),
        invoice_number=value("INV-001", "INV-001", 0.99),
    )

    rows = batch_identity_rows([first, second], ["licence.pdf", "invoice.pdf"])

    assert rows[0]["Document"] == "licence.pdf"
    assert rows[1]["Invoice"] == "INV-001"
    assert batch_identity_conflicts([first, second]) == []


def test_conflicting_supplier_batch_is_blocked():
    first = DocumentExtraction(
        supplier_name_en=value("Al Noor LLC", "Al Noor LLC", 0.98),
        trn=value("100123456700003", "100123456700003", 0.99),
        invoice_number=value("INV-001", "INV-001", 0.99),
    )
    second = DocumentExtraction(
        supplier_name_en=value("Different Supplier LLC", "Different Supplier LLC", 0.98),
        trn=value("100999999900003", "100999999900003", 0.99),
        invoice_number=value("INV-999", "INV-999", 0.99),
    )

    conflicts = batch_identity_conflicts([first, second])

    assert len(conflicts) == 3
    with pytest.raises(ValueError, match="Conflicting English supplier name"):
        merge_extractions([first, second])
