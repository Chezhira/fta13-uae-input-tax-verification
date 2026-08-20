"""Deterministic verification engine for FTA Decision No. 13 of 2026."""

from .models import (
    CheckKind, Evidence, HumanConclusion, PaymentMethod, PersonType,
    RiskEvent, Supplier, Supply, Verdict,
)
from .engine import evaluate_supplier, evaluate_supply, VerificationOutcome
from . import thresholds

__version__ = "0.1.0"
__all__ = [
    "CheckKind", "Evidence", "HumanConclusion", "PaymentMethod", "PersonType",
    "RiskEvent", "Supplier", "Supply", "Verdict",
    "evaluate_supplier", "evaluate_supply", "VerificationOutcome", "thresholds",
]
