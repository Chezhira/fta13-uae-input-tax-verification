# Copyright (c) 2026 Chez Solutions. Authored by Zahidah Murira.
# MIT License: https://github.com/Chezhira/fta13-uae-input-tax-verification

"""Deterministic threshold arithmetic. No AI, no heuristics, no I/O.

This module is the part an FTA officer or an auditor would recompute by hand.
Every number here traces to a clause. Keep it that way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

from .models import Supply

# Article 6(1): per-supply de minimis, consideration excluding VAT.
DE_MINIMIS_PER_SUPPLY = Decimal("10000")
# Article 6(2): supplier-level ceiling that withdraws the de minimis.
DE_MINIMIS_WITHDRAWAL = Decimal("100000")
# Article 3(4): supplier-level trigger for bank confirmation and adverse media review.
ENHANCED_CHECKS_TRIGGER = Decimal("375000")

# Article 3(3)(a)(1) and (2): "more than twice" over the previous 12 months.
RISK_EVENT_LIMIT = 2

def window_start(as_of: date) -> date:
    """Start of the rolling previous 12-month window.

    The Decision says "the previous 12 (twelve) months", not a tax year or a
    calendar year, so the window rolls with the assessment date.
    """
    # "12 months" is implemented as twelve calendar months, not 365 days.
    # The only exceptional date is 29 February, which maps to 28 February.
    try:
        return as_of.replace(year=as_of.year - 1)
    except ValueError:
        return as_of.replace(year=as_of.year - 1, day=28)


def trailing_total(
    supplies: Iterable[Supply],
    as_of: date,
    *,
    include_as_of_date: bool = True,
) -> Decimal:
    """Total consideration from one supplier over the previous 12 months.

    `include_as_of_date` controls whether supplies dated on the assessment date
    itself count toward the trailing figure. The Decision is silent. Default is
    inclusive, which is the conservative reading: it brings the threshold into
    play sooner.
    """
    start = window_start(as_of)
    total = Decimal("0")
    for s in supplies:
        if s.supply_date < start:
            continue
        if s.supply_date > as_of:
            continue
        if s.supply_date == as_of and not include_as_of_date:
            continue
        total += s.consideration_ex_vat
    return total


@dataclass(frozen=True)
class ThresholdAssessment:
    """Full, explainable threshold position for one supplier at one date."""

    as_of: date
    window_from: date
    trailing_12m: Decimal
    expected_forward_12m: Decimal
    consideration_ex_vat: Decimal | None

    de_minimis_available: bool          # Art 6(1) limb, before the 6(2) test
    de_minimis_withdrawn: bool          # Art 6(2) applies
    verification_required: bool         # net position for this supply
    enhanced_checks_required: bool      # Art 3(4)

    def basis(self) -> list[str]:
        """Human-readable trace. Goes straight into the verification register."""
        lines = [
            f"Window: {self.window_from.isoformat()} to {self.as_of.isoformat()}",
            f"Trailing 12m supplier spend: AED {self.trailing_12m:,.2f}",
            f"Expected forward 12m spend: AED {self.expected_forward_12m:,.2f}",
        ]
        if self.consideration_ex_vat is not None:
            lines.append(
                f"This supply (excl. VAT): AED {self.consideration_ex_vat:,.2f} "
                f"({'below' if self.de_minimis_available else 'at or above'} "
                f"the Art 6(1) de minimis of AED {DE_MINIMIS_PER_SUPPLY:,.0f})"
            )
        if self.de_minimis_withdrawn:
            lines.append(
                f"Art 6(2): supplier spend exceeds AED {DE_MINIMIS_WITHDRAWAL:,.0f}, "
                "so the Art 6(1) exception is withdrawn for every supply."
            )
        lines.append(
            f"Art 3(4) enhanced checks: "
            f"{'required' if self.enhanced_checks_required else 'not required'} "
            f"(trigger AED {ENHANCED_CHECKS_TRIGGER:,.0f})"
        )
        return lines


def assess(
    *,
    as_of: date,
    prior_supplies: Sequence[Supply],
    expected_forward_12m: Decimal = Decimal("0"),
    consideration_ex_vat: Decimal | None = None,
    include_as_of_date: bool = True,
) -> ThresholdAssessment:
    """Evaluate Articles 3(4) and 6 for one supplier, optionally for one supply.

    Pass `consideration_ex_vat=None` to assess the supplier position alone
    (onboarding, monthly monitoring). Pass a value to decide whether a specific
    supply needs Article 4 verification.
    """
    trailing = trailing_total(
        prior_supplies, as_of, include_as_of_date=include_as_of_date
    )

    # Both limbs are disjunctive and both use "exceeds", i.e. strictly greater.
    withdrawn = (
        trailing > DE_MINIMIS_WITHDRAWAL
        or expected_forward_12m > DE_MINIMIS_WITHDRAWAL
    )
    enhanced = (
        trailing > ENHANCED_CHECKS_TRIGGER
        or expected_forward_12m > ENHANCED_CHECKS_TRIGGER
    )

    if consideration_ex_vat is None:
        de_minimis_available = False
        required = True
    else:
        de_minimis_available = consideration_ex_vat < DE_MINIMIS_PER_SUPPLY
        required = not (de_minimis_available and not withdrawn)

    return ThresholdAssessment(
        as_of=as_of,
        window_from=window_start(as_of),
        trailing_12m=trailing,
        expected_forward_12m=expected_forward_12m,
        consideration_ex_vat=consideration_ex_vat,
        de_minimis_available=de_minimis_available,
        de_minimis_withdrawn=withdrawn,
        verification_required=required,
        enhanced_checks_required=enhanced,
    )


def crossing_date(
    supplies: Sequence[Supply], threshold: Decimal
) -> date | None:
    """First date on which rolling 12-month spend exceeded `threshold`.

    Used to answer the question the Decision does not: what about supplies you
    skipped verifying before the supplier crossed AED 100,000? See
    `retrospective_exposure`.
    """
    ordered = sorted(supplies, key=lambda s: s.supply_date)
    for s in ordered:
        if trailing_total(ordered, s.supply_date) > threshold:
            return s.supply_date
    return None


def retrospective_exposure(supplies: Sequence[Supply]) -> list[Supply]:
    """Supplies taken under the de minimis before the supplier crossed AED 100,000.

    The Decision does not say whether crossing the Article 6(2) ceiling reaches
    back. This function does not answer that question. It surfaces the population
    at issue so a human can take a documented position on it.
    """
    crossed = crossing_date(supplies, DE_MINIMIS_WITHDRAWAL)
    if crossed is None:
        return []
    return [
        s
        for s in sorted(supplies, key=lambda s: s.supply_date)
        if s.supply_date < crossed
        and s.consideration_ex_vat < DE_MINIMIS_PER_SUPPLY
    ]


def reverification_due(verified_on: date | None) -> date | None:
    """Article 5(1): re-verify where not verified in the previous 12 months."""
    if verified_on is None:
        return None
    try:
        return verified_on.replace(year=verified_on.year + 1)
    except ValueError:
        return verified_on.replace(year=verified_on.year + 1, day=28)


def needs_supplier_verification(verified_on: date | None, as_of: date) -> bool:
    """True on first dealing, or where the last verification is over 12 months old."""
    if verified_on is None:
        return True
    return verified_on <= window_start(as_of)


def count_risk_events(events: Iterable, kind: str, as_of: date) -> int:
    """Article 3(3)(a)(1) and (2) counters over the rolling window."""
    start = window_start(as_of)
    return sum(
        1 for e in events if e.kind == kind and start <= e.occurred_on <= as_of
    )
