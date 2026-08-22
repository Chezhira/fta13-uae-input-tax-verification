from datetime import date
from decimal import Decimal

from fta13.extraction import DocumentExtraction, ExtractedValue
from fta13.linkage import link_document_to_supplier
from fta13.portfolio import LedgerTransaction


def ledger(invoice="INV-1", amount="1000.00"):
    return LedgerTransaction(
        supplier_reference="V000145",
        supplier_name="ABC Trading LLC",
        supplier_trn="100123456700003",
        supply_reference=invoice,
        supply_date=date(2026, 7, 15),
        amount_excluding_vat=Decimal(amount),
    )


def extraction(**overrides):
    values = {
        "supplier_name_en": ExtractedValue(original="ABC Trading LLC", normalized="ABC Trading LLC"),
        "trn": ExtractedValue(original="100123456700003", normalized="100123456700003"),
        "invoice_number": ExtractedValue(original="INV-1", normalized="INV-1"),
        "invoice_date": ExtractedValue(original="2026-07-15", normalized="2026-07-15"),
        "consideration_ex_vat": ExtractedValue(original="1,000.00", normalized="1000.00"),
    }
    values.update(overrides)
    return DocumentExtraction(**values)


def test_exact_document_link_is_ready_but_not_human_confirmed():
    result = link_document_to_supplier(extraction(), [ledger()])

    assert result.can_confirm
    assert result.matched_supply_reference == "INV-1"
    assert result.match_basis == "Invoice reference"
    assert result.as_dict()["human_confirmed"] is False


def test_unique_date_and_amount_can_link_when_invoice_number_is_missing():
    result = link_document_to_supplier(
        extraction(invoice_number=ExtractedValue()), [ledger()]
    )

    assert result.can_confirm
    assert result.match_basis == "Invoice date and amount"


def test_trn_mismatch_blocks_confirmation():
    result = link_document_to_supplier(
        extraction(trn=ExtractedValue(original="999", normalized="999")), [ledger()]
    )

    assert not result.can_confirm
    assert any("TRN" in conflict for conflict in result.conflicts)


def test_matching_trn_allows_human_review_of_name_variation():
    result = link_document_to_supplier(
        extraction(
            supplier_name_en=ExtractedValue(
                original="ABC Trading Company LLC",
                normalized="ABC Trading Company LLC",
            )
        ),
        [ledger()],
    )

    assert result.can_confirm
    assert any("name differs" in warning for warning in result.warnings)


def test_matched_invoice_with_amount_difference_is_conflict():
    result = link_document_to_supplier(extraction(), [ledger(amount="1200.00")])

    assert not result.can_confirm
    assert any("Amount excluding VAT" in conflict for conflict in result.conflicts)
