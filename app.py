"""Public, no-upload readiness workflow for FTA Decision No. 13 of 2026."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta
from decimal import Decimal

import streamlit as st

from fta13.engine import VerificationOutcome, evaluate_supplier, evaluate_supply
from fta13.models import (
    Evidence,
    HumanConclusion,
    PaymentMethod,
    PersonType,
    RiskEvent,
    Supplier,
    Supply,
    Verdict,
)


st.set_page_config(
    page_title="FTA13 Readiness & Verification",
    page_icon="✓",
    layout="wide",
)

st.title("FTA Decision 13 Readiness & Verification")
st.caption(
    "Explore thresholds, complete the applicable supplier and supply checks, "
    "and export a review record."
)
st.warning(
    "Educational reference tool only. It assesses Decision No. 13 verification "
    "measures, not overall input-tax recoverability. Do not enter confidential "
    "or personal information. This app does not store data or accept document uploads."
)


def money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def evidence(kind: str, held: bool, as_of: date) -> list[Evidence]:
    if not held:
        return []
    return [
        Evidence(
            kind=kind,
            reference=f"user-confirmed:{kind}",
            obtained_on=as_of,
            note="Visitor confirmed that valid evidence is held; document not uploaded.",
        )
    ]


def conclusion(
    clause_id: str,
    satisfied: bool,
    rationale: str,
    reviewer: str,
    as_of: date,
) -> HumanConclusion | None:
    if not reviewer.strip() or not rationale.strip():
        return None
    return HumanConclusion(
        clause_id=clause_id,
        conclusion=satisfied,
        rationale=rationale.strip(),
        decided_by=reviewer.strip(),
        decided_on=as_of,
    )


def collect_conclusion(
    clause_id: str,
    label: str,
    reviewer: str,
    as_of: date,
    *,
    help_text: str = "",
) -> HumanConclusion | None:
    status = st.selectbox(
        label,
        ["Awaiting review", "Satisfied", "Not satisfied"],
        key=f"status_{clause_id}",
        help=help_text or None,
    )
    rationale = st.text_area(
        f"Rationale for {clause_id}",
        key=f"rationale_{clause_id}",
        placeholder="Record the facts reviewed and the basis for the conclusion.",
        height=80,
    )
    if status == "Awaiting review":
        return None
    return conclusion(
        clause_id,
        status == "Satisfied",
        rationale,
        reviewer,
        as_of,
    )


def result_table(outcome: VerificationOutcome) -> list[dict]:
    return [
        {
            "Clause": result.clause_id,
            "Article": result.article,
            "Requirement": result.requirement,
            "Route": result.kind.value.title(),
            "Status": result.verdict.value.replace("_", " ").title(),
            "Detail": result.detail,
        }
        for result in outcome.results
        if result.verdict is not Verdict.NOT_APPLICABLE
    ]


def render_outcome(title: str, outcome: VerificationOutcome) -> None:
    st.subheader(title)
    if not outcome.assessment.verification_required and outcome.supply_id:
        st.success(
            "The Article 6 exception is available for this scenario. Continue "
            "monitoring trailing and expected supplier totals."
        )
    elif outcome.decision_13_verification_complete:
        st.success("Applicable Decision 13 verification checks are complete.")
    else:
        st.error(
            f"{len(outcome.blocking_gaps)} blocking gap(s) remain before the "
            "applicable Decision 13 verification can be marked complete."
        )
    if outcome.warnings:
        for warning in outcome.warnings:
            st.warning(warning)
    rows = result_table(outcome)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")


def make_register_csv(
    supplier_outcome: VerificationOutcome,
    supply_outcome: VerificationOutcome,
) -> str:
    buffer = io.StringIO()
    fields = [
        "assessment_level",
        "supplier_id",
        "supply_id",
        "as_of",
        "verification_required",
        "enhanced_checks_required",
        "trailing_12m_aed",
        "expected_forward_12m_aed",
        "decision_13_verification_complete",
        "clause_id",
        "article",
        "requirement",
        "route",
        "status",
        "blocking",
        "detail",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    assessment_rows = (
        (("supply", supply_outcome),)
        if not supply_outcome.assessment.verification_required
        else (
            ("supplier", supplier_outcome),
            ("supply", supply_outcome),
        )
    )
    for level, outcome in assessment_rows:
        applicable = [
            result
            for result in outcome.results
            if result.verdict is not Verdict.NOT_APPLICABLE
        ]
        if not applicable:
            applicable = [None]
        for result in applicable:
            writer.writerow(
                {
                    "assessment_level": level,
                    "supplier_id": outcome.supplier_id,
                    "supply_id": outcome.supply_id or "",
                    "as_of": outcome.as_of.isoformat(),
                    "verification_required": outcome.assessment.verification_required,
                    "enhanced_checks_required": outcome.assessment.enhanced_checks_required,
                    "trailing_12m_aed": outcome.assessment.trailing_12m,
                    "expected_forward_12m_aed": outcome.assessment.expected_forward_12m,
                    "decision_13_verification_complete": (
                        outcome.decision_13_verification_complete
                    ),
                    "clause_id": result.clause_id if result else "",
                    "article": result.article if result else "",
                    "requirement": result.requirement if result else "",
                    "route": result.kind.value if result else "",
                    "status": result.verdict.value if result else "exception_available",
                    "blocking": result.blocking if result else "",
                    "detail": result.detail if result else "",
                }
            )
    return buffer.getvalue()


def make_markdown_report(
    supplier_outcome: VerificationOutcome,
    supply_outcome: VerificationOutcome,
) -> str:
    lines = [
        "# FTA Decision 13 Verification Record",
        "",
        f"Assessment date: {supply_outcome.as_of.isoformat()}",
        f"Supplier reference: {supplier_outcome.supplier_id}",
        f"Supply reference: {supply_outcome.supply_id}",
        "",
        "## Threshold assessment",
        "",
    ]
    lines.extend(f"- {item}" for item in supply_outcome.assessment.basis())
    report_sections = (
        (("Supply verification", supply_outcome),)
        if not supply_outcome.assessment.verification_required
        else (
            ("Supplier verification", supplier_outcome),
            ("Supply verification", supply_outcome),
        )
    )
    if not supply_outcome.assessment.verification_required:
        lines.extend(
            [
                "",
                "## Article 6 exception",
                "",
                "The Article 6 exception is available for this supply based on "
                "the values entered. Continue monitoring trailing and expected "
                "supplier totals.",
            ]
        )
    for title, outcome in report_sections:
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                (
                    "**Complete:** Yes"
                    if outcome.decision_13_verification_complete
                    else "**Complete:** No"
                ),
                "",
                "| Clause | Article | Status | Requirement | Detail |",
                "|---|---|---|---|---|",
            ]
        )
        for result in outcome.results:
            if result.verdict is Verdict.NOT_APPLICABLE:
                continue
            detail = result.detail.replace("|", "/").replace("\n", " ")
            requirement = result.requirement.replace("|", "/")
            lines.append(
                f"| {result.clause_id} | {result.article} | "
                f"{result.verdict.value} | {requirement} | {detail} |"
            )
    lines.extend(
        [
            "",
            "## Important limitation",
            "",
            "This record addresses FTA Decision No. 13 of 2026 verification "
            "measures only. It does not determine overall input-tax "
            "recoverability under UAE VAT law and is not tax advice.",
        ]
    )
    return "\n".join(lines)


tab_scenario, tab_supplier, tab_supply, tab_report = st.tabs(
    ["1. Scenario", "2. Supplier checks", "3. Supply checks", "4. Report"]
)

with tab_scenario:
    st.header("Scenario and thresholds")
    c1, c2 = st.columns(2)
    with c1:
        assessment_date = st.date_input(
            "Supply / assessment date",
            value=date(2026, 10, 1),
            min_value=date(2026, 10, 1),
        )
        supplier_ref = st.text_input(
            "Supplier reference",
            value="SUPPLIER-001",
            help="Use an internal reference, not a legal name.",
        )
        supply_ref = st.text_input(
            "Supply reference",
            value="SUPPLY-001",
            help="Use an internal invoice or transaction reference.",
        )
        person_type_label = st.selectbox(
            "Supplier type", ["Legal person", "Natural person"]
        )
        country = st.text_input(
            "Country of incorporation (ISO-2)",
            value="AE",
            max_chars=2,
        ).upper()
    with c2:
        invoice_value = st.number_input(
            "This supply, excluding VAT (AED)",
            min_value=0.0,
            value=2400.0,
            step=100.0,
        )
        trailing_spend = st.number_input(
            "Prior 12-month supplier spend (AED)",
            min_value=0.0,
            value=120000.0,
            step=1000.0,
        )
        forward_spend = st.number_input(
            "Expected next 12-month spend (AED)",
            min_value=0.0,
            value=150000.0,
            step=1000.0,
        )
        verified_on = st.date_input(
            "Last completed supplier verification",
            value=None,
            max_value=assessment_date,
            help="Leave blank where the supplier has not yet been verified.",
        )
        complete_now = st.checkbox(
            "Mark this assessment as the current supplier verification",
            help=(
                "Select only when the Article 3 checks in the Supplier tab have "
                "been completed and signed off."
            ),
        )
        reviewer = st.text_input(
            "Reviewer name",
            placeholder="Required to sign judgment conclusions",
        )

    threshold_supplier = Supplier(
        supplier_id=supplier_ref or "SUPPLIER-001",
        legal_name="Not collected",
        person_type=(
            PersonType.LEGAL
            if person_type_label == "Legal person"
            else PersonType.NATURAL
        ),
        country_of_incorporation=country or "AE",
        verified_on=assessment_date if complete_now else verified_on,
        expected_forward_12m=money(forward_spend),
    )
    threshold_supply = Supply(
        supply_id=supply_ref or "SUPPLY-001",
        supplier_id=threshold_supplier.supplier_id,
        supply_date=assessment_date,
        consideration_ex_vat=money(invoice_value),
    )
    history = []
    if trailing_spend:
        history.append(
            Supply(
                supply_id="AGGREGATED-PRIOR-SPEND",
                supplier_id=threshold_supplier.supplier_id,
                supply_date=assessment_date - timedelta(days=1),
                consideration_ex_vat=money(trailing_spend),
                description="Aggregate entered for public scenario assessment.",
            )
        )
    threshold_outcome = evaluate_supply(
        threshold_supplier,
        threshold_supply,
        prior_supplies=history,
    )
    a = threshold_outcome.assessment
    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Decision 13 verification",
        "Required" if a.verification_required else "Exception available",
    )
    m2.metric(
        "AED 100k ceiling",
        "Exceeded" if a.de_minimis_withdrawn else "Not exceeded",
    )
    m3.metric(
        "Enhanced supplier checks",
        "Required" if a.enhanced_checks_required else "Not required",
    )
    with st.expander("Threshold basis", expanded=True):
        for item in a.basis():
            st.write(f"• {item}")

with tab_supplier:
    st.header("Supplier verification: Article 3")
    st.caption(
        "Confirm whether evidence is held. Documents stay with you and are not uploaded."
    )
    supplier_evidence: list[Evidence] = []
    supplier_conclusions: list[HumanConclusion] = []

    if person_type_label == "Legal person":
        supplier_evidence += evidence(
            "certificate_of_incorporation",
            st.checkbox(
                "Incorporation verified through an official database or valid certificate"
            ),
            assessment_date,
        )
        item = collect_conclusion(
            "3.1.b.1.consistency",
            "Incorporation details agree with the name, address, employees and related information",
            reviewer,
            assessment_date,
        )
        if item:
            supplier_conclusions.append(item)
        supplier_evidence += evidence(
            "representative_id",
            st.checkbox(
                "Valid ID held for the authorised director, agent or employee"
            ),
            assessment_date,
        )
    else:
        supplier_evidence += evidence(
            "passport",
            st.checkbox("Valid proof of identity held for the natural-person supplier"),
            assessment_date,
        )
        supplier_evidence += evidence(
            "meeting_record",
            st.checkbox("In-person or virtual pre-supply meeting documented"),
            assessment_date,
        )

    supplier_evidence += evidence(
        "place_of_business_check",
        st.checkbox("Actual place of business verified electronically or by visit"),
        assessment_date,
    )
    item = collect_conclusion(
        "3.2.b",
        "Place of business is compatible with the supplier's activities",
        reviewer,
        assessment_date,
    )
    if item:
        supplier_conclusions.append(item)

    st.subheader("Risk indicators")
    rc1, rc2 = st.columns(2)
    with rc1:
        address_changes = st.number_input(
            "Address changes in previous 12 months",
            min_value=0,
            max_value=20,
            value=0,
        )
        address_justification = st.text_area(
            "Explanation if address changed more than twice",
            disabled=address_changes <= 2,
        )
    with rc2:
        employee_changes = st.number_input(
            "Key-employee changes in previous 12 months",
            min_value=0,
            max_value=20,
            value=0,
        )
        employee_justification = st.text_area(
            "Explanation if key employees changed more than twice",
            disabled=employee_changes <= 2,
        )
    item = collect_conclusion(
        "3.3.a.3",
        "Transactions are not disproportionate or unexpected relative to the supplier",
        reviewer,
        assessment_date,
    )
    if item:
        supplier_conclusions.append(item)

    if a.enhanced_checks_required:
        st.subheader("Enhanced checks: Article 3(4)")
        supplier_evidence += evidence(
            "bank_confirmation",
            st.checkbox("Written UAE bank confirmation is held"),
            assessment_date,
        )
        item = collect_conclusion(
            "3.4.a.validity",
            "Bank confirmation is from an authorised UAE bank and has no relevant reservations",
            reviewer,
            assessment_date,
        )
        if item:
            supplier_conclusions.append(item)
        item = collect_conclusion(
            "3.4.b",
            "Public reviews and media coverage have been assessed with no suspected tax-evasion indicator",
            reviewer,
            assessment_date,
        )
        if item:
            supplier_conclusions.append(item)

with tab_supply:
    st.header("Supply verification: Article 4")
    sc1, sc2 = st.columns(2)
    with sc1:
        payment_label = st.selectbox("Payment method", ["Electronic", "Cash"])
        third_party = st.checkbox("Third party involved in payment")
        offshore = st.checkbox("Payment account outside incorporation country")
    with sc2:
        is_goods = st.checkbox("This is a supply of goods", value=True)
        intermediary = st.checkbox("Supplier acts as an intermediary")
        description = st.text_input(
            "Plain-language supply description",
            placeholder="For example: replacement machine parts",
        )

    supply_evidence: list[Evidence] = []
    supply_conclusions: list[HumanConclusion] = []
    supply_judgments = [
        ("4.1.a", "General assessment of the transaction conditions is complete"),
        ("4.1.b", "Supplier engagement rests on genuine commercial reasons"),
        ("4.2.a", "Payment method and conditions are commercially justifiable"),
        ("4.3.a", "Price or margin is commercially justifiable against market conditions"),
        ("4.3.b", "Supply falls within the supplier's ordinary or licensed activities"),
    ]
    if third_party:
        supply_judgments.append(
            ("4.2.a.thirdparty", "Third-party payment involvement has a reasonable explanation")
        )
    if offshore:
        supply_judgments.append(
            ("4.2.a.offshore", "Offshore payee account has a reasonable commercial explanation")
        )
    if intermediary:
        supply_judgments.append(
            ("4.3.d", "Intermediary role has a clear and justifiable commercial explanation")
        )
    for clause_id, label in supply_judgments:
        item = collect_conclusion(
            clause_id,
            label,
            reviewer,
            assessment_date,
        )
        if item:
            supply_conclusions.append(item)

    if payment_label == "Cash":
        supply_evidence += evidence(
            "cash_payment_rationale",
            st.checkbox("Documented commercial reason for cash payment is held"),
            assessment_date,
        )
        item = collect_conclusion(
            "4.2.b.cash.compliance",
            "Cash payment is within applicable thresholds and easily verifiable",
            reviewer,
            assessment_date,
        )
        if item:
            supply_conclusions.append(item)
    if is_goods:
        supply_evidence += evidence(
            "origin_document",
            st.checkbox("Authenticity and origin evidence is held"),
            assessment_date,
        )
        supply_evidence += evidence(
            "title_document",
            st.checkbox("Supplier ownership or right-to-dispose evidence is held"),
            assessment_date,
        )

supplier_risk_events = [
    RiskEvent("address_change", assessment_date - timedelta(days=15 * (i + 1)))
    for i in range(address_changes)
] + [
    RiskEvent("key_employee_change", assessment_date - timedelta(days=15 * (i + 1)))
    for i in range(employee_changes)
]
risk_justifications = {}
if address_changes > 2 and address_justification.strip():
    risk_justifications["3.3.a.1"] = address_justification.strip()
if employee_changes > 2 and employee_justification.strip():
    risk_justifications["3.3.a.2"] = employee_justification.strip()

supplier = Supplier(
    supplier_id=supplier_ref or "SUPPLIER-001",
    legal_name="Not collected",
    person_type=(
        PersonType.LEGAL
        if person_type_label == "Legal person"
        else PersonType.NATURAL
    ),
    country_of_incorporation=country or "AE",
    verified_on=assessment_date if complete_now else verified_on,
    evidence=supplier_evidence,
    conclusions=supplier_conclusions,
    risk_events=supplier_risk_events,
    expected_forward_12m=money(forward_spend),
    risk_justifications=risk_justifications,
)
supply = Supply(
    supply_id=supply_ref or "SUPPLY-001",
    supplier_id=supplier.supplier_id,
    supply_date=assessment_date,
    consideration_ex_vat=money(invoice_value),
    description=description,
    payment_method=(
        PaymentMethod.CASH
        if payment_label == "Cash"
        else PaymentMethod.ELECTRONIC
    ),
    third_party_in_payment=third_party,
    payee_country="ZZ" if offshore else supplier.country_of_incorporation,
    supplier_is_intermediary=intermediary,
    is_goods=is_goods,
    evidence=supply_evidence,
    conclusions=supply_conclusions,
)

supplier_outcome = evaluate_supplier(
    supplier,
    as_of=assessment_date,
    prior_supplies=history,
)
supply_outcome = evaluate_supply(
    supplier,
    supply,
    prior_supplies=history,
)

with tab_supplier:
    render_outcome("Supplier result", supplier_outcome)

with tab_supply:
    render_outcome("Supply result", supply_outcome)

with tab_report:
    st.header("Verification record")
    exception_available = not supply_outcome.assessment.verification_required
    overall_complete = exception_available or (
        supplier_outcome.decision_13_verification_complete
        and supply_outcome.decision_13_verification_complete
    )
    r1, r2, r3 = st.columns(3)
    r1.metric(
        "Supplier checks",
        (
            "Not required for this supply"
            if exception_available
            else (
                "Complete"
                if supplier_outcome.decision_13_verification_complete
                else f"{len(supplier_outcome.blocking_gaps)} gap(s)"
            )
        ),
    )
    r2.metric(
        "Supply checks",
        "Complete"
        if supply_outcome.decision_13_verification_complete
        else f"{len(supply_outcome.blocking_gaps)} gap(s)",
    )
    r3.metric(
        "Overall Decision 13 status",
        "Exception available"
        if exception_available
        else ("Complete" if overall_complete else "Open"),
    )

    report = make_markdown_report(supplier_outcome, supply_outcome)
    register_csv = make_register_csv(supplier_outcome, supply_outcome)
    audit_json = json.dumps(
        {
            "supplier": supplier_outcome.register_row(),
            "supply": supply_outcome.register_row(),
            "generated_on": date.today().isoformat(),
            "scope_limitation": (
                "Decision 13 verification only; not an overall input-tax "
                "recoverability conclusion."
            ),
        },
        indent=2,
        default=str,
    )

    st.download_button(
        "Download readable assessment (.md)",
        data=report,
        file_name=f"fta13_assessment_{supply.supply_id}.md",
        mime="text/markdown",
        width="stretch",
    )
    dc1, dc2 = st.columns(2)
    with dc1:
        st.download_button(
            "Download verification register (.csv)",
            data=register_csv,
            file_name=f"fta13_register_{supply.supply_id}.csv",
            mime="text/csv",
            width="stretch",
        )
    with dc2:
        st.download_button(
            "Download machine-readable record (.json)",
            data=audit_json,
            file_name=f"fta13_record_{supply.supply_id}.json",
            mime="application/json",
            width="stretch",
        )
    with st.expander("Preview readable assessment"):
        st.markdown(report)

with st.expander("Interpretation and privacy notes"):
    st.markdown(
        """
        - Threshold comparisons follow the Decision's strict wording: below
          AED 10,000 and exceeds AED 100,000 / AED 375,000.
        - The lookback is implemented as twelve calendar months.
        - The public app records visitor assertions that evidence is held. It
          does not inspect, validate, upload or retain the documents.
        - Forward expected spend can trigger checks before historic spend
          reaches a threshold.
        - The implementation was reconciled to the authoritative Arabic
          Decision and uses the FTA's unofficial English translation for
          English-language labels. If any discrepancy arises, the Arabic text prevails.
        """
    )
