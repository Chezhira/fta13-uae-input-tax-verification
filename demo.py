"""Worked example: a supplier that looks fine on the invoice and is not.

Run: python demo.py
"""

from datetime import date, timedelta
from decimal import Decimal as D

from fta13 import (
    Evidence, HumanConclusion, PaymentMethod, PersonType,
    RiskEvent, Supplier, Supply, evaluate_supplier, evaluate_supply,
)

TODAY = date(2026, 11, 15)


def rule(t):
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def show(out):
    for line in out.assessment.basis():
        print(f"  {line}")
    print()
    for r in out.results:
        if r.verdict.value == "not_applicable":
            continue
        mark = {"satisfied": "OK  ", "missing": "GAP ", "failed": "FAIL"}[r.verdict.value]
        block = "" if r.blocking else "  (advisory)"
        print(f"  {mark} {r.article:<12} {r.requirement[:58]}{block}")
        if r.detail:
            print(f"       -> {r.detail[:88]}")
    for w in out.warnings:
        print(f"\n  ! {w}")
    label = "SUPPLIER VERIFICATION COMPLETE" if out.supply_id is None else "DECISION 13 VERIFICATION COMPLETE"
    print(f"\n  {label}: {out.decision_13_verification_complete}")


# A supplier onboarded on a framework agreement worth AED 600k a year.
supplier = Supplier(
    supplier_id="SUP-1042",
    legal_name="Meridian General Trading LLC",
    person_type=PersonType.LEGAL,
    country_of_incorporation="AE",
    licensed_activities=["general trading", "building materials"],
    verified_on=None,
    expected_forward_12m=D("600000"),
    evidence=[
        Evidence("certificate_of_incorporation", "DMS/SUP-1042/COI", TODAY - timedelta(days=3)),
        Evidence("representative_id", "DMS/SUP-1042/ID", TODAY - timedelta(days=3),
                 expires_on=date(2029, 4, 1)),
        Evidence("place_of_business_check", "DMS/SUP-1042/SITE", TODAY - timedelta(days=2),
                 note="Warehouse, Al Quoz. Photographs on file."),
    ],
    risk_events=[
        RiskEvent("address_change", TODAY - timedelta(days=300)),
        RiskEvent("address_change", TODAY - timedelta(days=180)),
        RiskEvent("address_change", TODAY - timedelta(days=40)),
    ],
)

rule("1. SUPPLIER ONBOARDING (Article 3)")
print("Nothing has been bought yet. Trailing spend is nil.")
print("The forward expectation alone pulls this supplier into enhanced checks.\n")
show(evaluate_supplier(supplier, as_of=TODAY, prior_supplies=[]))

rule("2. AFTER REMEDIATION")
supplier.evidence.append(
    Evidence("bank_confirmation", "DMS/SUP-1042/BANK", TODAY - timedelta(days=1),
             note="Unqualified. Issued to the supplier, not to us. Art 3(4)(a) permits this.")
)
supplier.risk_justifications["3.3.a.1"] = (
    "Three relocations follow a landlord dispute and a documented warehouse "
    "consolidation. Tenancy contracts and the settlement agreement are on file."
)
for cid, rationale in [
    ("3.1.b.1.consistency", "Registry extract agrees to trade licence and letterhead."),
    ("3.2.b", "Warehouse capacity consistent with building materials distribution."),
    ("3.3.a.3", "Order pattern consistent with two years of trading history."),
    ("3.4.a.validity", "Confirmation is from an authorised UAE bank and contains no reservations."),
    ("3.4.b", "No adverse media. Two trade directory listings, both consistent."),
]:
    supplier.conclusions.append(
        HumanConclusion(cid, True, rationale, "F. Controller", TODAY)
    )
supplier.verified_on = TODAY
show(evaluate_supplier(supplier, as_of=TODAY, prior_supplies=[]))

rule("3. A SMALL SUPPLY, ELEVEN MONTHS LATER (Articles 4 and 6)")
later = TODAY + timedelta(days=330)
history = [
    Supply(f"INV-{i}", "SUP-1042", TODAY + timedelta(days=30 * i), D("48000"))
    for i in range(1, 11)
]
small = Supply(
    "INV-11", "SUP-1042", later, D("2400"),
    description="Replacement fixings, 3 pallets",
    payment_method=PaymentMethod.CASH,
    payee_country="AE",
)
print("Invoice is AED 2,400, comfortably under the Art 6(1) de minimis of 10,000.")
print("It is still in full scope, because trailing spend has passed 100,000.\n")
show(evaluate_supply(supplier, small, prior_supplies=history))

rule("4. WHAT THE REGISTER RECORDS")
out = evaluate_supply(supplier, small, prior_supplies=history)
row = out.register_row()
for k in ("supplier_id", "supply_id", "as_of", "verification_required",
          "enhanced_checks_required", "trailing_12m", "blocking_gaps",
          "open_judgments", "decision_13_verification_complete"):
    print(f"  {k:<26} {row[k]}")
