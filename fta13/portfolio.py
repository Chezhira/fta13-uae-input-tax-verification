"""Deterministic portfolio screening for accounts-payable ledger exports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from .models import Supply
from .thresholds import (
    DE_MINIMIS_WITHDRAWAL,
    assess,
    crossing_date,
    needs_supplier_verification,
    retrospective_exposure,
    reverification_due,
    window_start,
)


class PortfolioValidationError(ValueError):
    """A ledger row cannot be screened without correction."""


@dataclass(frozen=True)
class LedgerTransaction:
    supplier_reference: str
    supply_reference: str
    supply_date: date
    amount_excluding_vat: Decimal
    input_vat: Decimal = Decimal("0")
    expected_next_12m: Decimal = Decimal("0")
    last_verified_on: date | None = None


@dataclass(frozen=True)
class PortfolioResult:
    supplier_reference: str
    transaction_count: int
    trailing_12m_aed: Decimal
    expected_next_12m_aed: Decimal
    priority: str
    threshold_position: str
    first_crossing_100k: date | None
    enhanced_checks_required: bool
    supplier_verification_due: bool
    last_verified_on: date | None
    next_reverification_due: date | None
    due_within_90_days: bool
    supplies_requiring_verification: int
    input_vat_screening_exposure_aed: Decimal
    retrospective_supply_count: int
    retrospective_input_vat_aed: Decimal

    def as_row(self) -> dict[str, object]:
        row = asdict(self)
        for key, value in row.items():
            if isinstance(value, Decimal):
                row[key] = f"{value:.2f}"
            elif isinstance(value, date):
                row[key] = value.isoformat()
            elif value is None:
                row[key] = ""
        return row


def _text(value: object) -> str:
    return str(value or "").strip()


def _money(value: object, *, field: str, row_number: int, optional: bool) -> Decimal:
    text = _text(value).replace(",", "")
    if not text and optional:
        return Decimal("0")
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise PortfolioValidationError(
            f"Row {row_number}: {field} must be a valid AED amount."
        ) from exc
    if amount < 0:
        raise PortfolioValidationError(
            f"Row {row_number}: {field} cannot be negative."
        )
    return amount


def _date(value: object, *, field: str, row_number: int, optional: bool) -> date | None:
    text = _text(value)
    if not text and optional:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise PortfolioValidationError(
            f"Row {row_number}: {field} must use YYYY-MM-DD."
        ) from exc


def parse_ledger_rows(
    rows: Iterable[Mapping[str, object]],
    column_map: Mapping[str, str | None],
) -> list[LedgerTransaction]:
    """Parse mapped CSV rows without guessing dates, money, or identifiers."""
    required = (
        "supplier_reference",
        "supply_reference",
        "supply_date",
        "amount_excluding_vat",
    )
    missing = [field for field in required if not column_map.get(field)]
    if missing:
        raise PortfolioValidationError(
            "Map every required field: " + ", ".join(missing) + "."
        )

    parsed = []
    seen_supply_refs: set[tuple[str, str]] = set()
    for row_number, row in enumerate(rows, start=2):
        supplier = _text(row.get(str(column_map["supplier_reference"])))
        supply = _text(row.get(str(column_map["supply_reference"])))
        if not supplier or not supply:
            raise PortfolioValidationError(
                f"Row {row_number}: supplier and supply references are required."
            )
        identity = (supplier, supply)
        if identity in seen_supply_refs:
            raise PortfolioValidationError(
                f"Row {row_number}: duplicate supply reference {supply!r} for {supplier!r}."
            )
        seen_supply_refs.add(identity)

        def value(field: str) -> object:
            source = column_map.get(field)
            return row.get(source, "") if source else ""

        parsed.append(
            LedgerTransaction(
                supplier_reference=supplier,
                supply_reference=supply,
                supply_date=_date(
                    value("supply_date"),
                    field="supply_date",
                    row_number=row_number,
                    optional=False,
                ),
                amount_excluding_vat=_money(
                    value("amount_excluding_vat"),
                    field="amount_excluding_vat",
                    row_number=row_number,
                    optional=False,
                ),
                input_vat=_money(
                    value("input_vat"),
                    field="input_vat",
                    row_number=row_number,
                    optional=True,
                ),
                expected_next_12m=_money(
                    value("expected_next_12m"),
                    field="expected_next_12m",
                    row_number=row_number,
                    optional=True,
                ),
                last_verified_on=_date(
                    value("last_verified_on"),
                    field="last_verified_on",
                    row_number=row_number,
                    optional=True,
                ),
            )
        )
    if not parsed:
        raise PortfolioValidationError("The ledger contains no data rows.")
    return parsed


def _one_supplier_value(
    items: list[LedgerTransaction], field: str, default: object
) -> object:
    populated = {getattr(item, field) for item in items if getattr(item, field) != default}
    if len(populated) > 1:
        raise PortfolioValidationError(
            f"Supplier {items[0].supplier_reference!r} has conflicting {field} values."
        )
    return next(iter(populated), default)


def screen_portfolio(
    transactions: Iterable[LedgerTransaction], *, as_of: date
) -> list[PortfolioResult]:
    """Rank suppliers using the same strict threshold logic as the main engine."""
    grouped: dict[str, list[LedgerTransaction]] = {}
    for item in transactions:
        if item.supply_date <= as_of:
            grouped.setdefault(item.supplier_reference, []).append(item)
    if not grouped:
        raise PortfolioValidationError("No transactions fall on or before the as-of date.")

    results = []
    for supplier_ref, items in grouped.items():
        expected = _one_supplier_value(items, "expected_next_12m", Decimal("0"))
        verified = _one_supplier_value(items, "last_verified_on", None)
        supplies = [
            Supply(
                supply_id=item.supply_reference,
                supplier_id=supplier_ref,
                supply_date=item.supply_date,
                consideration_ex_vat=item.amount_excluding_vat,
            )
            for item in items
        ]
        position = assess(
            as_of=as_of,
            prior_supplies=supplies,
            expected_forward_12m=expected,
        )
        in_window = [
            item for item in items if window_start(as_of) <= item.supply_date <= as_of
        ]
        verification_count = sum(
            1
            for item in in_window
            if assess(
                as_of=as_of,
                prior_supplies=supplies,
                expected_forward_12m=expected,
                consideration_ex_vat=item.amount_excluding_vat,
            ).verification_required
        )
        exposure = sum(
            (item.input_vat for item in in_window), Decimal("0")
        ) if position.de_minimis_withdrawn else sum(
            (
                item.input_vat
                for item in in_window
                if item.amount_excluding_vat >= Decimal("10000")
            ),
            Decimal("0"),
        )
        retrospective = retrospective_exposure(supplies)
        retrospective_ids = {item.supply_id for item in retrospective}
        retrospective_vat = sum(
            (item.input_vat for item in items if item.supply_reference in retrospective_ids),
            Decimal("0"),
        )
        due = reverification_due(verified)
        verification_due = needs_supplier_verification(verified, as_of)
        due_soon = due is not None and as_of < due <= as_of + timedelta(days=90)
        if position.enhanced_checks_required:
            priority = "1 - Enhanced checks"
            threshold_label = "Exceeds AED 375,000"
        elif position.de_minimis_withdrawn:
            priority = "2 - Full verification"
            threshold_label = "Exceeds AED 100,000"
        elif verification_count:
            priority = "3 - Transaction checks"
            threshold_label = "Below supplier ceiling; one or more supplies are AED 10,000+"
        else:
            priority = "4 - Monitor"
            threshold_label = "Below current thresholds"
        results.append(
            PortfolioResult(
                supplier_reference=supplier_ref,
                transaction_count=len(in_window),
                trailing_12m_aed=position.trailing_12m,
                expected_next_12m_aed=expected,
                priority=priority,
                threshold_position=threshold_label,
                first_crossing_100k=crossing_date(supplies, DE_MINIMIS_WITHDRAWAL),
                enhanced_checks_required=position.enhanced_checks_required,
                supplier_verification_due=verification_due,
                last_verified_on=verified,
                next_reverification_due=due,
                due_within_90_days=due_soon,
                supplies_requiring_verification=verification_count,
                input_vat_screening_exposure_aed=exposure,
                retrospective_supply_count=len(retrospective),
                retrospective_input_vat_aed=retrospective_vat,
            )
        )
    return sorted(
        results,
        key=lambda item: (
            int(item.priority[0]),
            -item.input_vat_screening_exposure_aed,
            item.supplier_reference,
        ),
    )
