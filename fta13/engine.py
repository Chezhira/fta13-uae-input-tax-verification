# Copyright (c) 2026 Chez Solutions. Authored by Zahidah Murira.
# MIT License: https://github.com/Chezhira/fta13-uae-input-tax-verification

"""Evaluation engine. Deterministic end to end.

The engine never calls a model. It consumes structured facts, evidence and
signed human conclusions, and returns a reproducible verdict. Two runs over the
same inputs always produce the same output, which is what Article 5(3) needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Sequence

from . import thresholds as T
from .clauses import Clause, SUPPLIER_CLAUSES, SUPPLY_CLAUSES
from .models import CheckKind, Evidence, Supplier, Supply, Verdict


@dataclass
class Context:
    supplier: Supplier
    supply: Supply | None
    as_of: date
    enhanced_checks_required: bool


@dataclass
class ClauseResult:
    clause_id: str
    article: str
    requirement: str
    kind: CheckKind
    verdict: Verdict
    blocking: bool
    detail: str = ""


@dataclass
class VerificationOutcome:
    supplier_id: str
    supply_id: str | None
    as_of: date
    assessment: T.ThresholdAssessment
    results: list[ClauseResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def blocking_gaps(self) -> list[ClauseResult]:
        return [
            r
            for r in self.results
            if r.blocking and r.verdict in (Verdict.MISSING, Verdict.FAILED)
        ]

    @property
    def decision_13_verification_complete(self) -> bool:
        """Whether the applicable Decision 13 checks have been completed.

        This is deliberately not an overall input-tax recoverability verdict.
        Other requirements of UAE VAT law remain outside this engine.
        """
        return not self.blocking_gaps

    @property
    def open_judgments(self) -> list[ClauseResult]:
        return [
            r
            for r in self.results
            if r.kind is CheckKind.JUDGMENT and r.verdict is Verdict.MISSING
        ]

    def register_row(self) -> dict:
        """Flat record for the Article 5(3) verification register."""
        return {
            "supplier_id": self.supplier_id,
            "supply_id": self.supply_id,
            "as_of": self.as_of.isoformat(),
            "verification_required": self.assessment.verification_required,
            "enhanced_checks_required": self.assessment.enhanced_checks_required,
            "trailing_12m": str(self.assessment.trailing_12m),
            "expected_forward_12m": str(self.assessment.expected_forward_12m),
            "clauses_applicable": sum(
                1 for r in self.results if r.verdict is not Verdict.NOT_APPLICABLE
            ),
            "clauses_satisfied": sum(
                1 for r in self.results if r.verdict is Verdict.SATISFIED
            ),
            "blocking_gaps": [r.clause_id for r in self.blocking_gaps],
            "open_judgments": [r.clause_id for r in self.open_judgments],
            "decision_13_verification_complete": self.decision_13_verification_complete,
            "threshold_basis": self.assessment.basis(),
            "warnings": self.warnings,
        }


def _has_evidence(
    pool: Sequence[Evidence], kinds: tuple[str, ...], as_of: date
) -> tuple[bool, str]:
    if not kinds:
        return False, "no evidence kind configured"
    for e in pool:
        if e.kind in kinds:
            if e.is_valid_on(as_of):
                return True, f"{e.kind} ref {e.reference}"
            return False, f"{e.kind} ref {e.reference} not valid at {as_of.isoformat()}"
    return False, f"none of {', '.join(kinds)} on file"


def _conclusion(pool, clause_id: str):
    for c in pool:
        if c.clause_id == clause_id:
            return c
    return None


def _evaluate(clause: Clause, ctx: Context) -> ClauseResult:
    base = dict(
        clause_id=clause.id,
        article=clause.article,
        requirement=clause.requirement,
        kind=clause.kind,
        blocking=clause.blocking,
    )

    if not clause.applies_when(ctx):
        return ClauseResult(**base, verdict=Verdict.NOT_APPLICABLE)

    target = ctx.supplier if clause.level == "supplier" else ctx.supply

    if clause.kind is CheckKind.DOCUMENT:
        ok, detail = _has_evidence(target.evidence, clause.evidence_kinds, ctx.as_of)
        return ClauseResult(
            **base,
            verdict=Verdict.SATISFIED if ok else Verdict.MISSING,
            detail=detail,
        )

    if clause.kind is CheckKind.COMPUTED:
        return _evaluate_computed(clause, ctx, base)

    # JUDGMENT: satisfied only by a signed human conclusion.
    c = _conclusion(target.conclusions, clause.id)
    if c is None:
        return ClauseResult(
            **base, verdict=Verdict.MISSING, detail="awaiting human sign-off"
        )
    return ClauseResult(
        **base,
        verdict=Verdict.SATISFIED if c.conclusion else Verdict.FAILED,
        detail=f"{c.decided_by} on {c.decided_on.isoformat()}: {c.rationale}"
        + (f" [AI draft {c.ai_draft_id}]" if c.ai_draft_id else ""),
    )


_RISK_KIND = {"3.3.a.1": "address_change", "3.3.a.2": "key_employee_change"}


def _evaluate_computed(clause: Clause, ctx: Context, base: dict) -> ClauseResult:
    kind = _RISK_KIND[clause.id]
    n = T.count_risk_events(ctx.supplier.risk_events, kind, ctx.as_of)
    triggered = n > T.RISK_EVENT_LIMIT
    if not triggered:
        return ClauseResult(
            **base, verdict=Verdict.SATISFIED, detail=f"{n} in previous 12 months"
        )
    # Article 3(3)(b): a triggered indicator is not a bar. It demands justification.
    just = ctx.supplier.risk_justifications.get(clause.id)
    if just:
        return ClauseResult(
            **base,
            verdict=Verdict.SATISFIED,
            detail=f"{n} in previous 12 months; Art 3(3)(b) justification held: {just}",
        )
    return ClauseResult(
        **base,
        verdict=Verdict.FAILED,
        detail=f"{n} in previous 12 months (limit {T.RISK_EVENT_LIMIT}); "
        "Art 3(3)(b) justification required and not held",
    )


def evaluate_supplier(
    supplier: Supplier,
    *,
    as_of: date,
    prior_supplies: Sequence[Supply] = (),
) -> VerificationOutcome:
    _validate_prior_supplies(supplier.supplier_id, prior_supplies)
    a = T.assess(
        as_of=as_of,
        prior_supplies=prior_supplies,
        expected_forward_12m=supplier.expected_forward_12m,
        consideration_ex_vat=None,
    )
    ctx = Context(supplier, None, as_of, a.enhanced_checks_required)
    out = VerificationOutcome(supplier.supplier_id, None, as_of, a)
    out.results = [_evaluate(c, ctx) for c in SUPPLIER_CLAUSES]

    if T.needs_supplier_verification(supplier.verified_on, as_of):
        due = T.reverification_due(supplier.verified_on)
        out.warnings.append(
            "Art 5(1): supplier verification required "
            + (
                "(no prior verification on file)"
                if due is None
                else f"(last verified {supplier.verified_on}, due {due})"
            )
        )
        out.results.append(
            ClauseResult(
                clause_id="5.1",
                article="5(1)",
                requirement="Supplier verified on first dealing, or within 12 months.",
                kind=CheckKind.COMPUTED,
                verdict=Verdict.FAILED,
                blocking=True,
                detail=f"last verified: {supplier.verified_on}",
            )
        )
    return out


def evaluate_supply(
    supplier: Supplier,
    supply: Supply,
    *,
    prior_supplies: Sequence[Supply] = (),
) -> VerificationOutcome:
    as_of = supply.supply_date
    if supply.supplier_id != supplier.supplier_id:
        raise ValueError("supply.supplier_id does not match supplier.supplier_id")
    _validate_prior_supplies(supplier.supplier_id, prior_supplies)
    prior_supplies = tuple(s for s in prior_supplies if s.supply_id != supply.supply_id)
    a = T.assess(
        as_of=as_of,
        prior_supplies=prior_supplies,
        expected_forward_12m=supplier.expected_forward_12m,
        consideration_ex_vat=supply.consideration_ex_vat,
    )
    out = VerificationOutcome(supplier.supplier_id, supply.supply_id, as_of, a)

    if not a.verification_required:
        out.warnings.append(
            "Art 6(1): out of scope, consideration below AED 10,000 and supplier "
            "spend below the AED 100,000 ceiling. Monitor monthly."
        )
        return out

    ctx = Context(supplier, supply, as_of, a.enhanced_checks_required)
    out.results = [_evaluate(c, ctx) for c in SUPPLY_CLAUSES]

    if T.needs_supplier_verification(supplier.verified_on, as_of):
        out.warnings.append(
            "Art 5(1): supplier not verified in the previous 12 months. "
            "Run supplier verification before claiming input tax on this supply."
        )
        out.results.append(
            ClauseResult(
                clause_id="5.1",
                article="5(1)",
                requirement="Supplier verified on first dealing, or within 12 months.",
                kind=CheckKind.COMPUTED,
                verdict=Verdict.FAILED,
                blocking=True,
                detail=f"last verified: {supplier.verified_on}",
            )
        )

    exposure = T.retrospective_exposure(list(prior_supplies))
    if exposure:
        out.warnings.append(
            f"Art 6(2) is silent on retrospection: {len(exposure)} earlier supplies "
            f"were taken under the de minimis before this supplier crossed AED "
            f"{T.DE_MINIMIS_WITHDRAWAL:,.0f}. Take a documented position."
        )
    return out


def _validate_prior_supplies(supplier_id: str, supplies: Sequence[Supply]) -> None:
    mixed = sorted({s.supplier_id for s in supplies if s.supplier_id != supplier_id})
    if mixed:
        raise ValueError(
            "prior_supplies contains other suppliers: " + ", ".join(mixed)
        )
