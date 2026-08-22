# Copyright (c) 2026 Chez Solutions. Authored by Zahidah Murira.
# MIT License: https://github.com/Chezhira/fta13-uae-input-tax-verification

"""Domain models for FTA Decision No. 13 of 2026 verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class PersonType(str, Enum):
    NATURAL = "natural"
    LEGAL = "legal"


class PaymentMethod(str, Enum):
    ELECTRONIC = "electronic"
    CASH = "cash"


class CheckKind(str, Enum):
    """How a clause is satisfied. Drives which layer owns it."""

    DOCUMENT = "document"    # deterministic: evidence of a given type exists and is valid
    COMPUTED = "computed"    # deterministic: engine derives the answer from structured data
    JUDGMENT = "judgment"    # requires a human conclusion; AI may draft, never decide


class Verdict(str, Enum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


@dataclass(frozen=True)
class Evidence:
    """A retained document or record. `sha256` gives Article 5(3) traceability."""

    kind: str                      # e.g. "passport", "certificate_of_incorporation"
    reference: str                 # storage path or document management reference
    obtained_on: date
    sha256: Optional[str] = None
    expires_on: Optional[date] = None
    note: str = ""

    def is_valid_on(self, as_of: date) -> bool:
        if self.obtained_on > as_of:
            return False
        return self.expires_on is None or self.expires_on >= as_of


@dataclass
class HumanConclusion:
    """A signed-off judgment. This, not an AI output, is what the record rests on."""

    clause_id: str
    conclusion: bool
    rationale: str
    decided_by: str
    decided_on: date
    ai_draft_id: Optional[str] = None   # links to the advisory draft, if one was used

    def __post_init__(self) -> None:
        if not self.decided_by.strip():
            raise ValueError("decided_by must name the responsible reviewer")
        if not self.rationale.strip():
            raise ValueError("rationale must document the reviewer's basis")


@dataclass
class RiskEvent:
    """An address or key-employee change, used for the Article 3(3) counters."""

    kind: str                      # "address_change" | "key_employee_change"
    occurred_on: date
    detail: str = ""


@dataclass
class Supply:
    supply_id: str
    supplier_id: str
    supply_date: date
    consideration_ex_vat: Decimal
    description: str = ""
    payment_method: PaymentMethod = PaymentMethod.ELECTRONIC
    third_party_in_payment: bool = False
    payee_country: Optional[str] = None       # None means same as supplier incorporation
    supplier_is_intermediary: bool = False
    is_goods: bool = True
    evidence: list[Evidence] = field(default_factory=list)
    conclusions: list[HumanConclusion] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.consideration_ex_vat < 0:
            raise ValueError("consideration_ex_vat cannot be negative")


@dataclass
class Supplier:
    supplier_id: str
    legal_name: str
    person_type: PersonType
    country_of_incorporation: str = "AE"
    licensed_activities: list[str] = field(default_factory=list)
    verified_on: Optional[date] = None
    evidence: list[Evidence] = field(default_factory=list)
    conclusions: list[HumanConclusion] = field(default_factory=list)
    risk_events: list[RiskEvent] = field(default_factory=list)
    expected_forward_12m: Decimal = Decimal("0")   # contracted or forecast spend
    risk_justifications: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.expected_forward_12m < 0:
            raise ValueError("expected_forward_12m cannot be negative")
