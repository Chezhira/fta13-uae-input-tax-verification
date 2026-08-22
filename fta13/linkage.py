"""Deterministic linkage between AP-ledger rows and extracted documents."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from .portfolio import LedgerTransaction


def _identity(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)


def _trn(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


@dataclass(frozen=True)
class LinkageComparison:
    field: str
    ledger_value: str
    document_value: str
    result: str


@dataclass(frozen=True)
class DocumentLinkage:
    supplier_reference: str
    supplier_name: str
    supplier_trn: str
    matched_supply_reference: str | None
    document_invoice_number: str
    match_basis: str
    status: str
    conflicts: tuple[str, ...]
    warnings: tuple[str, ...]
    comparisons: tuple[LinkageComparison, ...]

    @property
    def can_confirm(self) -> bool:
        return self.matched_supply_reference is not None and not self.conflicts

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["human_confirmed"] = False
        return result


def _document_name(item: Any) -> str:
    return (
        item.supplier_name_en.normalized
        or item.supplier_name_en.original
        or item.supplier_name_ar.normalized
        or item.supplier_name_ar.original
    ).strip()


def _document_value(item: Any, field: str) -> str:
    value = getattr(item, field)
    return (value.normalized or value.original).strip()


def _comparison(
    field: str,
    ledger_value: object,
    document_value: object,
    *,
    normalizer=_identity,
) -> LinkageComparison:
    ledger = str(ledger_value or "")
    document = str(document_value or "")
    if not ledger or not document:
        result = "Not available"
    elif normalizer(ledger) == normalizer(document):
        result = "Match"
    else:
        result = "Mismatch"
    return LinkageComparison(field, ledger, document, result)


def link_document_to_supplier(
    item: Any,
    ledger_rows: Iterable[LedgerTransaction],
) -> DocumentLinkage:
    """Propose one auditable link; a human confirmation is still required."""
    rows = list(ledger_rows)
    if not rows:
        raise ValueError("ledger_rows must contain the selected supplier")
    supplier_refs = {row.supplier_reference for row in rows}
    if len(supplier_refs) != 1:
        raise ValueError("ledger_rows must contain one supplier only")
    supplier_reference = rows[0].supplier_reference
    supplier_names = {row.supplier_name for row in rows if row.supplier_name}
    supplier_trns = {row.supplier_trn for row in rows if row.supplier_trn}
    if len(supplier_names) > 1 or len(supplier_trns) > 1:
        raise ValueError("selected supplier has conflicting identity values")
    supplier_name = next(iter(supplier_names), "")
    supplier_trn = next(iter(supplier_trns), "")

    document_name = _document_name(item)
    document_trn = _document_value(item, "trn")
    invoice_number = _document_value(item, "invoice_number")
    document_date: date | None = item.invoice_date_value()
    document_amount: Decimal | None = item.decimal_value()
    comparisons = [
        _comparison("Supplier legal name", supplier_name, document_name),
        _comparison("TRN", supplier_trn, document_trn, normalizer=_trn),
    ]
    conflicts: list[str] = []
    warnings: list[str] = []
    name_comparison, trn_comparison = comparisons
    if trn_comparison.result == "Mismatch":
        conflicts.append("TRN does not match the selected supplier.")
    elif trn_comparison.result == "Not available":
        warnings.append("TRN could not be compared.")
    if name_comparison.result == "Mismatch":
        if trn_comparison.result == "Match":
            warnings.append(
                "Supplier legal name differs, but the TRN matches. Confirm the "
                "name variation before accepting the link."
            )
        else:
            conflicts.append(
                "Supplier legal name does not match and no matching TRN resolves the identity."
            )
    elif name_comparison.result == "Not available":
        warnings.append("Supplier legal name could not be compared.")

    reference_matches = [
        row for row in rows if invoice_number and _identity(row.supply_reference) == _identity(invoice_number)
    ]
    date_amount_matches = [
        row
        for row in rows
        if document_date is not None
        and document_amount is not None
        and row.supply_date == document_date
        and row.amount_excluding_vat == document_amount
    ]
    matched: LedgerTransaction | None = None
    match_basis = "No unique transaction match"
    if len(reference_matches) == 1:
        matched = reference_matches[0]
        match_basis = "Invoice reference"
    elif len(date_amount_matches) == 1:
        matched = date_amount_matches[0]
        match_basis = "Invoice date and amount"
    elif len(reference_matches) > 1 or len(date_amount_matches) > 1:
        conflicts.append("More than one ledger transaction matches the document.")
    else:
        conflicts.append("No ledger transaction matches the document.")

    if matched is not None:
        invoice_comparison = _comparison(
            "Invoice reference", matched.supply_reference, invoice_number
        )
        date_comparison = _comparison(
            "Invoice date",
            matched.supply_date.isoformat(),
            document_date.isoformat() if document_date else "",
        )
        amount_comparison = _comparison(
            "Amount excluding VAT",
            f"{matched.amount_excluding_vat:.2f}",
            f"{document_amount:.2f}" if document_amount is not None else "",
        )
        comparisons.extend([invoice_comparison, date_comparison, amount_comparison])
        for comparison in (invoice_comparison, date_comparison, amount_comparison):
            if comparison.result == "Mismatch":
                conflicts.append(
                    f"{comparison.field} differs from the matched ledger transaction."
                )
            elif comparison.result == "Not available":
                warnings.append(f"{comparison.field} could not be compared.")

    return DocumentLinkage(
        supplier_reference=supplier_reference,
        supplier_name=supplier_name,
        supplier_trn=supplier_trn,
        matched_supply_reference=(matched.supply_reference if matched else None),
        document_invoice_number=invoice_number,
        match_basis=match_basis,
        status="Ready for human confirmation" if matched and not conflicts else "Conflict",
        conflicts=tuple(dict.fromkeys(conflicts)),
        warnings=tuple(dict.fromkeys(warnings)),
        comparisons=tuple(comparisons),
    )
