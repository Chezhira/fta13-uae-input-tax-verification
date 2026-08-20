"""Clause registry: every operative requirement of FTA Decision No. 13 of 2026.

`kind` decides which layer owns the clause:
  DOCUMENT / COMPUTED -> the deterministic engine decides, and its answer is final.
  JUDGMENT            -> a named human decides. AI may draft the rationale.

`applies_when` is a predicate over a Context, evaluated deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .models import CheckKind, PaymentMethod, PersonType


@dataclass(frozen=True)
class Clause:
    id: str
    article: str
    requirement: str
    kind: CheckKind
    level: str                                   # "supplier" | "supply"
    evidence_kinds: tuple[str, ...] = ()         # for DOCUMENT clauses
    applies_when: Callable[..., bool] = lambda ctx: True
    blocking: bool = True                        # unsatisfied -> verification incomplete
    note: str = ""


# --- Article 3: supplier ----------------------------------------------------

SUPPLIER_CLAUSES: list[Clause] = [
    Clause(
        "3.1.a.1", "3(1)(a)(1)",
        "Copy of valid proof of identity of the supplier (Emirates ID or passport).",
        CheckKind.DOCUMENT, "supplier",
        evidence_kinds=("emirates_id", "passport"),
        applies_when=lambda ctx: ctx.supplier.person_type is PersonType.NATURAL,
    ),
    Clause(
        "3.1.a.2", "3(1)(a)(2)",
        "Supplier met in person or virtually before the supply is made.",
        CheckKind.DOCUMENT, "supplier",
        evidence_kinds=("meeting_record",),
        applies_when=lambda ctx: ctx.supplier.person_type is PersonType.NATURAL,
    ),
    Clause(
        "3.1.b.1", "3(1)(b)(1)",
        "Incorporation verified via official database or certificate obtained.",
        CheckKind.DOCUMENT, "supplier",
        evidence_kinds=("certificate_of_incorporation", "registry_extract"),
        applies_when=lambda ctx: ctx.supplier.person_type is PersonType.LEGAL,
    ),
    Clause(
        "3.1.b.1.consistency", "3(1)(b)(1)",
        "Incorporation details consistent with the entity's name, address, "
        "employees and other related information.",
        CheckKind.JUDGMENT, "supplier",
        applies_when=lambda ctx: ctx.supplier.person_type is PersonType.LEGAL,
    ),
    Clause(
        "3.1.b.2", "3(1)(b)(2)",
        "Valid proof of identity of the authorised director, agent or employee.",
        CheckKind.DOCUMENT, "supplier",
        evidence_kinds=("representative_id",),
        applies_when=lambda ctx: ctx.supplier.person_type is PersonType.LEGAL,
    ),
    Clause(
        "3.2.a", "3(2)(a)",
        "Existence of an actual place of business verified electronically or by field visit.",
        CheckKind.DOCUMENT, "supplier",
        evidence_kinds=("place_of_business_check", "site_visit_report"),
    ),
    Clause(
        "3.2.b", "3(2)(b)",
        "Place of business compatible with the nature of the supplier's activities.",
        CheckKind.JUDGMENT, "supplier",
    ),
    Clause(
        "3.3.a.1", "3(3)(a)(1)",
        "Address not changed more than twice in the previous 12 months.",
        CheckKind.COMPUTED, "supplier",
        note="If triggered, Art 3(3)(b) justification is required instead.",
    ),
    Clause(
        "3.3.a.2", "3(3)(a)(2)",
        "Key employees not changed more than twice in the previous 12 months.",
        CheckKind.COMPUTED, "supplier",
        note="If triggered, Art 3(3)(b) justification is required instead.",
    ),
    Clause(
        "3.3.a.3", "3(3)(a)(3)",
        "No transactions disproportionate or unexpected in volume, value or nature "
        "relative to the supplier's size and trading history.",
        CheckKind.JUDGMENT, "supplier",
        note="Statistics can flag it. Only a human can conclude on it.",
    ),
    Clause(
        "3.4.a", "3(4)(a)",
        "Written confirmation from an authorised bank in the State that the supplier "
        "holds an account, free of relevant reservations or conditions.",
        CheckKind.DOCUMENT, "supplier",
        evidence_kinds=("bank_confirmation",),
        applies_when=lambda ctx: ctx.enhanced_checks_required,
    ),
    Clause(
        "3.4.a.validity", "3(4)(a)",
        "Bank confirmation is issued by an authorised bank in the State and has "
        "no relevant reservations or conditions.",
        CheckKind.JUDGMENT, "supplier",
        applies_when=lambda ctx: ctx.enhanced_checks_required,
    ),
    Clause(
        "3.4.b", "3(4)(b)",
        "Public reviews and media coverage reviewed; consistent with the nature and "
        "size of the business, with no indicators of suspected tax evasion.",
        CheckKind.JUDGMENT, "supplier",
        applies_when=lambda ctx: ctx.enhanced_checks_required,
    ),
]

# --- Article 4: supply ------------------------------------------------------

SUPPLY_CLAUSES: list[Clause] = [
    Clause(
        "4.1.a", "4(1)(a)",
        "General assessment of the supply's conditions completed.",
        CheckKind.JUDGMENT, "supply",
    ),
    Clause(
        "4.1.b", "4(1)(b)",
        "Supplier's engagement in the transaction rests on genuine commercial reasons.",
        CheckKind.JUDGMENT, "supply",
    ),
    Clause(
        "4.2.a", "4(2)(a)",
        "Payment method and conditions justifiable for commercial reasons.",
        CheckKind.JUDGMENT, "supply",
    ),
    Clause(
        "4.2.a.thirdparty", "4(2)(a)",
        "Reasonable commercial explanation for third-party involvement in payment, "
        "not contradicted by other evidence held.",
        CheckKind.JUDGMENT, "supply",
        applies_when=lambda ctx: ctx.supply.third_party_in_payment,
    ),
    Clause(
        "4.2.a.offshore", "4(2)(a)",
        "Reasonable commercial explanation for payment to an account outside the "
        "supplier's country of incorporation.",
        CheckKind.JUDGMENT, "supply",
        applies_when=lambda ctx: (
            ctx.supply.payee_country is not None
            and ctx.supply.payee_country != ctx.supplier.country_of_incorporation
        ),
    ),
    Clause(
        "4.2.b.cash", "4(2)(b)",
        "Cash payment supported by a documented commercial reason.",
        CheckKind.DOCUMENT, "supply",
        evidence_kinds=("cash_payment_rationale",),
        applies_when=lambda ctx: ctx.supply.payment_method is PaymentMethod.CASH,
    ),
    Clause(
        "4.2.b.cash.compliance", "4(2)(b)",
        "Cash payment is within applicable legislative thresholds and is easily verifiable.",
        CheckKind.JUDGMENT, "supply",
        applies_when=lambda ctx: ctx.supply.payment_method is PaymentMethod.CASH,
    ),
    Clause(
        "4.3.a", "4(3)(a)",
        "Prices or margin not commercially unjustifiable, nor significantly off "
        "market without a clear reason.",
        CheckKind.JUDGMENT, "supply",
    ),
    Clause(
        "4.3.b", "4(3)(b)",
        "Supply falls within the supplier's ordinary activity and commercial licence.",
        CheckKind.JUDGMENT, "supply",
        note="AI-assistable: compare invoice line description to licensed activities.",
    ),
    Clause(
        "4.3.c.origin", "4(3)(c)",
        "Authenticity and origin of the goods verified.",
        CheckKind.DOCUMENT, "supply",
        evidence_kinds=("origin_document",),
        applies_when=lambda ctx: ctx.supply.is_goods,
    ),
    Clause(
        "4.3.c.title", "4(3)(c)",
        "Supplier ownership of the goods or right to dispose of them verified.",
        CheckKind.DOCUMENT, "supply",
        evidence_kinds=("title_document",),
        applies_when=lambda ctx: ctx.supply.is_goods,
    ),
    Clause(
        "4.3.d", "4(3)(d)",
        "Clear and justifiable commercial explanation for the supplier's role as "
        "intermediary.",
        CheckKind.JUDGMENT, "supply",
        applies_when=lambda ctx: ctx.supply.supplier_is_intermediary,
    ),
]

ALL_CLAUSES = {c.id: c for c in (*SUPPLIER_CLAUSES, *SUPPLY_CLAUSES)}
