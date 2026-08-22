import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fta13.portfolio import (
    LedgerTransaction,
    PortfolioValidationError,
    parse_ledger_rows,
    screen_portfolio,
)


AS_OF = date(2026, 10, 1)


def tx(ref: str, amount: str, vat: str = "0", **kwargs) -> LedgerTransaction:
    return LedgerTransaction(
        supplier_reference=kwargs.pop("supplier_reference", "SUP-1"),
        supply_reference=ref,
        supply_date=kwargs.pop("supply_date", AS_OF),
        amount_excluding_vat=Decimal(amount),
        input_vat=Decimal(vat),
        **kwargs,
    )


def test_portfolio_ranks_enhanced_and_calculates_screening_exposure():
    rows = [tx("A", "200000", "10000"), tx("B", "180001", "9000")]
    result = screen_portfolio(rows, as_of=AS_OF)[0]

    assert result.priority == "1 - Enhanced checks"
    assert result.trailing_12m_aed == Decimal("380001")
    assert result.input_vat_screening_exposure_aed == Decimal("19000")
    assert result.supplies_requiring_verification == 2


def test_transaction_and_result_rows_are_export_ready():
    item = tx(
        "A",
        "12000.50",
        "600.03",
        supplier_reference="V1",
        expected_next_12m=Decimal("50000"),
        last_verified_on=date(2026, 1, 1),
    )
    transaction_row = item.as_row()
    result_row = screen_portfolio([item], as_of=AS_OF)[0].as_row()

    assert transaction_row["amount_excluding_vat"] == "12000.50"
    assert transaction_row["last_verified_on"] == "2026-01-01"
    assert result_row["trailing_12m_aed"] == "12000.50"
    assert result_row["next_reverification_due"] == "2027-01-01"


def test_strict_100k_boundary_and_expected_spend_trigger():
    at_boundary = screen_portfolio([tx("A", "100000", "5000")], as_of=AS_OF)[0]
    forward_trigger = screen_portfolio(
        [tx("B", "1000", "50", expected_next_12m=Decimal("100001"))],
        as_of=AS_OF,
    )[0]

    assert at_boundary.threshold_position.startswith("Below supplier ceiling")
    assert forward_trigger.priority == "2 - Full verification"
    assert forward_trigger.input_vat_screening_exposure_aed == Decimal("50")


def test_portfolio_surfaces_retrospective_population():
    rows = [
        tx("SMALL", "9000", "450", supply_date=date(2026, 1, 1)),
        tx("CROSS", "92000", "4600", supply_date=date(2026, 2, 1)),
    ]
    result = screen_portfolio(rows, as_of=AS_OF)[0]

    assert result.first_crossing_100k == date(2026, 2, 1)
    assert result.retrospective_supply_count == 1
    assert result.retrospective_input_vat_aed == Decimal("450")


def test_parser_maps_columns_and_rejects_conflicting_supplier_values():
    mapping = {
        "supplier_reference": "Vendor",
        "supply_reference": "Invoice",
        "supply_date": "Date",
        "amount_excluding_vat": "Net",
        "input_vat": "VAT",
        "expected_next_12m": "Forecast",
        "last_verified_on": None,
    }
    parsed = parse_ledger_rows(
        [
            {"Vendor": "S1", "Invoice": "I1", "Date": "2026-01-01", "Net": "1,000", "VAT": "50", "Forecast": "90000"},
            {"Vendor": "S1", "Invoice": "I2", "Date": "2026-02-01", "Net": "2000", "VAT": "100", "Forecast": "95000"},
        ],
        mapping,
    )
    with pytest.raises(PortfolioValidationError, match="conflicting expected_next_12m"):
        screen_portfolio(parsed, as_of=AS_OF)


def test_launch_dataset_demonstrates_retrospective_and_reverification_scenarios():
    dataset = Path(__file__).parents[1] / "examples" / "fta13_synthetic_portfolio_800.csv"
    with dataset.open(encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    mapping = {field: field for field in raw_rows[0]}
    results = screen_portfolio(parse_ledger_rows(raw_rows, mapping), as_of=AS_OF)

    retrospective = [item for item in results if item.retrospective_supply_count]
    due_soon = [item for item in results if item.due_within_90_days]
    overdue = [
        item
        for item in results
        if item.supplier_verification_due and item.last_verified_on is not None
    ]

    assert len(results) == 800
    assert len(retrospective) >= 12
    assert len(due_soon) >= 12
    assert len(overdue) >= 12
