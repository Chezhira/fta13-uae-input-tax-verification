"""Bilingual, document-assisted workflow for FTA Decision No. 13 of 2026."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import streamlit as st

from fta13 import __version__ as ENGINE_VERSION
from fta13.clauses import ALL_CLAUSES
from fta13.engine import VerificationOutcome, evaluate_supplier, evaluate_supply
from fta13.extraction import (
    DocumentExtraction,
    UploadValidationError,
    batch_identity_conflicts,
    batch_identity_rows,
    extract_document,
    merge_extractions,
    validate_upload,
)
from fta13.models import (
    CheckKind,
    Evidence,
    HumanConclusion,
    PaymentMethod,
    PersonType,
    RiskEvent,
    Supplier,
    Supply,
    Verdict,
)
from fta13.reporting import build_pdf_report


AI_EVIDENCE_HINTS = "ai_proposed_evidence"

EVIDENCE_KEYS = {
    "certificate_of_incorporation": "evidence_incorporation",
    "representative_id": "evidence_representative_id",
    "passport": "evidence_passport",
    "meeting_record": "evidence_meeting",
    "place_of_business_check": "evidence_business_place",
    "bank_confirmation": "evidence_bank",
    "cash_payment_rationale": "evidence_cash_reason",
    "origin_document": "evidence_origin",
    "title_document": "evidence_title",
}


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
    "measures, not overall input-tax recoverability. AI-extracted information must "
    "be reviewed by a person before it is relied upon."
)


def setting(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, os.getenv(name, default)))
    except Exception:
        return os.getenv(name, default)


RULESET_LABEL = setting("FTA13_RULESET", f"fta13 {ENGINE_VERSION}")

SESSION_DEFAULTS = {
    "assessment_date_input": date(2026, 10, 1),
    "supplier_ref_input": "SUPPLIER-001",
    "supply_ref_input": "SUPPLY-001",
    "country_input": "AE",
    "invoice_value_input": 2400.0,
    "payment_method_input": "Electronic",
    "is_goods_input": True,
    "offshore_input": False,
}
for session_key, default_value in SESSION_DEFAULTS.items():
    st.session_state.setdefault(session_key, default_value)


def apply_extraction(item: DocumentExtraction) -> None:
    """Prefill only supported values; the visible widgets remain human-editable."""
    mapping = {
        "supplier_ref_input": item.supplier_reference.normalized or item.supplier_reference.original,
        "supply_ref_input": item.invoice_number.normalized or item.invoice_number.original,
        "country_input": item.country_of_incorporation.normalized,
        "description_input": item.supply_description.original,
    }
    for key, value in mapping.items():
        if value:
            st.session_state[key] = value
    amount = item.decimal_value()
    if amount is not None and amount >= 0:
        # Streamlit number_input requires int/float widget state. The value is
        # converted back through Decimal(str(value)) in money(), preserving the
        # displayed two-decimal currency amount without binary-float arithmetic.
        st.session_state["invoice_value_input"] = float(amount)
    extracted_date = item.invoice_date_value()
    if extracted_date and extracted_date >= date(2026, 10, 1):
        st.session_state["assessment_date_input"] = extracted_date
    method = item.payment_method.normalized.lower()
    if method in {"cash", "نقد", "نقداً", "نقدا"}:
        st.session_state["payment_method_input"] = "Cash"
    elif method:
        st.session_state["payment_method_input"] = "Electronic"
    payee_country = item.payee_country.normalized.strip().upper()
    supplier_country = item.country_of_incorporation.normalized.strip().upper()
    if payee_country and supplier_country:
        st.session_state["offshore_input"] = payee_country != supplier_country
    for key, value in {
        "is_goods_input": item.is_goods,
        "third_party_input": item.third_party_in_payment,
        "intermediary_input": item.supplier_is_intermediary,
    }.items():
        if value is not None:
            st.session_state[key] = value
    # Extraction proposes evidence; it never asserts a blocking DOCUMENT clause.
    st.session_state[AI_EVIDENCE_HINTS] = sorted(
        EVIDENCE_KEYS[kind]
        for kind in item.evidence_kinds
        if kind in EVIDENCE_KEYS
    )
    # A newly extracted document set always requires a fresh human review.
    st.session_state["ai_extraction_reviewed"] = False


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


def evidence_checkbox(label: str, key: str) -> bool:
    """Surface an AI evidence hint without answering the control question."""
    proposed = key in st.session_state.get(AI_EVIDENCE_HINTS, [])
    if proposed:
        label = f"{label}  ·  AI found a matching document. Confirm it yourself."
    return st.checkbox(label, key=key)


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
    clause = ALL_CLAUSES.get(clause_id)
    if clause is None:
        raise KeyError(f"unknown clause {clause_id}")
    if clause.kind is not CheckKind.JUDGMENT:
        raise ValueError(f"{clause_id} is not a human-judgment clause")
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
    if not reviewer.strip():
        st.error(
            f"Reviewer name is required before the {clause_id} conclusion can "
            "be recorded. Enter it in the Scenario tab."
        )
        return None
    if not rationale.strip():
        st.error(
            f"A rationale is required before the {clause_id} conclusion can be recorded."
        )
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


def render_outcome(
    title: str,
    outcome: VerificationOutcome,
    *,
    exception_available: bool = False,
) -> None:
    st.subheader(title)
    if exception_available and outcome.supply_id is None:
        st.success(
            "Supplier verification is not required for this supply because the "
            "Article 6 exception is available. Continue monitoring trailing and "
            "expected supplier totals."
        )
        return
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
    *,
    reviewer: str,
    generated_on_utc: str,
) -> str:
    lines = [
        "# FTA Decision 13 Verification Record",
        "",
        f"Assessment date: {supply_outcome.as_of.isoformat()}",
        f"Ruleset version: {RULESET_LABEL}",
        f"Generated on (UTC): {generated_on_utc}",
        f"Reviewer (self-declared, not verified by this tool): {reviewer or 'not stated'}",
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


openai_key = setting("OPENAI_API_KEY")
extraction_model = setting("OPENAI_EXTRACTION_MODEL", "gpt-5.6")
supabase_url = setting("SUPABASE_URL")
supabase_key = setting("SUPABASE_ANON_KEY")

st.subheader("Document assistant | مساعد المستندات")
st.caption(
    "Upload Arabic, English or bilingual documents for one supplier and one "
    "supply/invoice only. AI compares identities, proposes fields and provides "
    "page-level sources for human review."
)
uploaded_documents = st.file_uploader(
    "Documents for one supplier and one supply",
    type=["pdf", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
    help="Up to five documents, maximum 5 MB each.",
)
language_label = st.selectbox(
    "Document language",
    ["Detect automatically", "Arabic | العربية", "English", "Arabic + English"],
)
language_hint = {
    "Detect automatically": "auto",
    "Arabic | العربية": "ar",
    "English": "en",
    "Arabic + English": "mixed",
}[language_label]
ai_consent = st.checkbox(
    "I am authorised to process these documents and understand they will be sent "
    "to the configured AI provider for extraction.",
    key="ai_processing_consent",
)
single_batch_confirmed = st.checkbox(
    "I confirm that all uploaded documents relate to the same supplier and the "
    "same supply/invoice.",
    key="single_supplier_batch_confirmed",
)
extract_disabled = (
    not uploaded_documents
    or not ai_consent
    or not single_batch_confirmed
    or not openai_key
)
if not openai_key:
    st.info("AI extraction is disabled until OPENAI_API_KEY is configured.")
if st.button("Read documents in Arabic and English", disabled=extract_disabled):
    if len(uploaded_documents) > 5:
        st.error("Upload no more than five documents per assessment.")
    else:
        extractions = []
        with st.spinner("Reading the documents and locating supporting fields..."):
            try:
                for uploaded in uploaded_documents:
                    content = uploaded.getvalue()
                    validate_upload(uploaded.name, uploaded.type, content)
                    result = extract_document(
                        filename=uploaded.name,
                        mime_type=uploaded.type,
                        content=content,
                        api_key=openai_key,
                        model=extraction_model,
                        language_hint=language_hint,
                    )
                    extractions.append(result)
                st.session_state["document_extractions"] = [
                    item.model_dump(mode="json") for item in extractions
                ]
                filenames = [uploaded.name for uploaded in uploaded_documents]
                st.session_state["batch_identity_rows"] = batch_identity_rows(
                    extractions, filenames
                )
                conflicts = batch_identity_conflicts(extractions)
                st.session_state["batch_identity_conflicts"] = conflicts
                if conflicts:
                    st.session_state.pop("merged_extraction", None)
                    st.rerun()
                merged_extraction = merge_extractions(extractions)
                st.session_state["merged_extraction"] = merged_extraction.model_dump(
                    mode="json"
                )
                apply_extraction(merged_extraction)
                st.success("Extraction complete. Review every populated field below.")
                st.rerun()
            except UploadValidationError as exc:
                st.error(f"Upload rejected: {exc}")
            except Exception as exc:
                st.error(f"Document extraction failed: {exc}")

if st.session_state.get("batch_identity_rows"):
    st.markdown("**Document identity comparison**")
    st.dataframe(
        st.session_state["batch_identity_rows"], hide_index=True, width="stretch"
    )
for conflict in st.session_state.get("batch_identity_conflicts", []):
    st.error(
        f"Batch blocked: {conflict} Start a separate assessment for each "
        "supplier or invoice."
    )

merged_extraction = None
if st.session_state.get("merged_extraction"):
    merged_extraction = DocumentExtraction.model_validate(
        st.session_state["merged_extraction"]
    )
    languages = ", ".join(
        {"ar": "Arabic", "en": "English", "mixed": "Arabic + English", "unknown": "Unknown"}.get(code, code)
        for code in merged_extraction.detected_languages
    )
    st.success(f"Detected language(s): {languages or 'Not established'}")
    rows = merged_extraction.review_rows()
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    source_rows = merged_extraction.source_rows()
    if source_rows:
        with st.expander("Page-level source quotes | اقتباسات المصدر"):
            st.dataframe(source_rows, hide_index=True, width="stretch")
    for warning in merged_extraction.warnings:
        st.warning(warning)
    extraction_reviewed = st.checkbox(
        "I reviewed and corrected the AI-populated fields against the documents.",
        key="ai_extraction_reviewed",
    )
    if not extraction_reviewed:
        st.warning("Review confirmation is required before this assessment can be saved.")
else:
    extraction_reviewed = True


tab_scenario, tab_supplier, tab_supply, tab_report = st.tabs(
    ["1. Scenario", "2. Supplier checks", "3. Supply checks", "4. Report"]
)

with tab_scenario:
    st.header("Scenario and thresholds")
    c1, c2 = st.columns(2)
    with c1:
        assessment_date = st.date_input(
            "Supply / assessment date",
            min_value=date(2026, 10, 1),
            key="assessment_date_input",
        )
        supplier_ref = st.text_input(
            "Supplier reference",
            help="Use an internal reference, not a legal name.",
            key="supplier_ref_input",
        )
        supply_ref = st.text_input(
            "Supply reference",
            help="Use an internal invoice or transaction reference.",
            key="supply_ref_input",
        )
        person_type_label = st.selectbox(
            "Supplier type", ["Legal person", "Natural person"]
        )
        country = st.text_input(
            "Country of incorporation (ISO-2)",
            max_chars=2,
            key="country_input",
        ).upper()
    with c2:
        invoice_value = st.number_input(
            "This supply, excluding VAT (AED)",
            min_value=0.0,
            step=100.0,
            key="invoice_value_input",
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
        verified_on=verified_on,
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
        "Confirm whether valid evidence is held. AI suggestions do not tick these "
        "boxes; review each supporting document yourself."
    )
    supplier_evidence: list[Evidence] = []
    supplier_conclusions: list[HumanConclusion] = []

    if person_type_label == "Legal person":
        supplier_evidence += evidence(
            "certificate_of_incorporation",
            evidence_checkbox(
                "Incorporation verified through an official database or valid certificate",
                key="evidence_incorporation",
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
            evidence_checkbox(
                "Valid ID held for the authorised director, agent or employee",
                key="evidence_representative_id",
            ),
            assessment_date,
        )
    else:
        supplier_evidence += evidence(
            "passport",
            evidence_checkbox(
                "Valid proof of identity held for the natural-person supplier",
                key="evidence_passport",
            ),
            assessment_date,
        )
        supplier_evidence += evidence(
            "meeting_record",
            evidence_checkbox(
                "In-person or virtual pre-supply meeting documented",
                key="evidence_meeting",
            ),
            assessment_date,
        )

    supplier_evidence += evidence(
        "place_of_business_check",
        evidence_checkbox(
            "Actual place of business verified electronically or by visit",
            key="evidence_business_place",
        ),
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
            evidence_checkbox("Written UAE bank confirmation is held", "evidence_bank"),
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
        payment_label = st.selectbox(
            "Payment method", ["Electronic", "Cash"], key="payment_method_input"
        )
        third_party = st.checkbox(
            "Third party involved in payment", key="third_party_input"
        )
        offshore = st.checkbox(
            "Payment account outside incorporation country", key="offshore_input"
        )
    with sc2:
        is_goods = st.checkbox(
            "This is a supply of goods", key="is_goods_input"
        )
        intermediary = st.checkbox(
            "Supplier acts as an intermediary", key="intermediary_input"
        )
        description = st.text_input(
            "Plain-language supply description",
            placeholder="For example: replacement machine parts",
            key="description_input",
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
            evidence_checkbox(
                "Documented commercial reason for cash payment is held",
                key="evidence_cash_reason",
            ),
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
            evidence_checkbox(
                "Authenticity and origin evidence is held", key="evidence_origin"
            ),
            assessment_date,
        )
        supply_evidence += evidence(
            "title_document",
            evidence_checkbox(
                "Supplier ownership or right-to-dispose evidence is held",
                key="evidence_title",
            ),
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
    verified_on=verified_on,
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
if complete_now:
    # Article 5(1) is the record created by a completed Article 3 assessment,
    # so it cannot be used to block that same assessment from being recorded.
    article_3_gaps = [
        gap for gap in supplier_outcome.blocking_gaps if gap.clause_id != "5.1"
    ]
    if not article_3_gaps:
        supplier.verified_on = assessment_date
        st.session_state.pop("complete_now_blocked", None)
        supplier_outcome = evaluate_supplier(
            supplier,
            as_of=assessment_date,
            prior_supplies=history,
        )
    else:
        st.session_state["complete_now_blocked"] = len(article_3_gaps)
else:
    st.session_state.pop("complete_now_blocked", None)
supply_outcome = evaluate_supply(
    supplier,
    supply,
    prior_supplies=history,
)
exception_available = not supply_outcome.assessment.verification_required

with tab_supplier:
    blocked = st.session_state.get("complete_now_blocked")
    if blocked:
        st.warning(
            "This assessment cannot yet stand as the supplier verification: "
            f"{blocked} blocking gap(s) remain in the Article 3 checks."
        )
    render_outcome(
        "Supplier result",
        supplier_outcome,
        exception_available=exception_available,
    )

with tab_supply:
    render_outcome("Supply result", supply_outcome)

with tab_report:
    st.header("Verification record")
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

    generated_on_utc = datetime.now(timezone.utc).isoformat()
    report = make_markdown_report(
        supplier_outcome,
        supply_outcome,
        reviewer=reviewer,
        generated_on_utc=generated_on_utc,
    )
    register_csv = make_register_csv(supplier_outcome, supply_outcome)
    audit_json = json.dumps(
        {
            "supplier": supplier_outcome.register_row(),
            "supply": supply_outcome.register_row(),
            "ruleset_version": RULESET_LABEL,
            "generated_on_utc": generated_on_utc,
            "reviewer": reviewer or "not stated",
            "reviewer_identity_verified": False,
            "scope_limitation": (
                "Decision 13 verification only; not an overall input-tax "
                "recoverability conclusion."
            ),
        },
        indent=2,
        default=str,
    )

    pdf_report = build_pdf_report(
        supplier_outcome=supplier_outcome,
        supply_outcome=supply_outcome,
        extracted_document=(
            merged_extraction.model_dump(mode="json") if merged_extraction else None
        ),
        ruleset_label=RULESET_LABEL,
        generated_on_utc=generated_on_utc,
        reviewer=reviewer,
        exception_available=exception_available,
    )

    exports_locked = merged_extraction is not None and not extraction_reviewed
    if exports_locked:
        st.error(
            "Confirm that you have reviewed the AI-populated fields before "
            "exporting a verification record."
        )

    st.download_button(
        "Download professional verification report (.pdf)",
        data=pdf_report,
        file_name=f"fta13_verification_{supply.supply_id}.pdf",
        mime="application/pdf",
        width="stretch",
        disabled=exports_locked,
    )
    dc1, dc2 = st.columns(2)
    with dc1:
        st.download_button(
            "Download verification register (.csv)",
            data=register_csv,
            file_name=f"fta13_register_{supply.supply_id}.csv",
            mime="text/csv",
            width="stretch",
            disabled=exports_locked,
        )
    with dc2:
        st.download_button(
            "Download machine-readable record (.json)",
            data=audit_json,
            file_name=f"fta13_record_{supply.supply_id}.json",
            mime="application/json",
            width="stretch",
            disabled=exports_locked,
        )
    with st.expander("Save for later (optional)"):
        st.caption(
            "Sign-in is required only to store this assessment and its documents "
            "privately. Downloads and anonymous use do not require an account."
        )
        if not (supabase_url and supabase_key):
            st.info("Private saving is not configured on this deployment.")
        elif "supabase_session" not in st.session_state:
            login_email = st.text_input("Email for secure sign-in")
            if st.button("Email me a sign-in code", disabled=not login_email):
                try:
                    from fta13.storage import request_email_otp

                    request_email_otp(supabase_url, supabase_key, login_email)
                    st.session_state["otp_email"] = login_email
                    st.success("Check your email for the one-time sign-in code.")
                except Exception as exc:
                    st.error(f"Sign-in code could not be sent: {exc}")
            if st.session_state.get("otp_email"):
                otp_code = st.text_input("One-time code", max_chars=12)
                if st.button("Verify code", disabled=not otp_code):
                    try:
                        from fta13.storage import verify_email_otp

                        st.session_state["supabase_session"] = verify_email_otp(
                            supabase_url,
                            supabase_key,
                            st.session_state["otp_email"],
                            otp_code,
                        )
                        del st.session_state["otp_email"]
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Code verification failed: {exc}")
        else:
            st.success("Signed in. You can now save this assessment privately.")
            if st.button(
                "Save assessment and documents privately",
                width="stretch",
                disabled=not extraction_reviewed,
            ):
                try:
                    from fta13.storage import SupabaseStore

                    session = st.session_state["supabase_session"]
                    store = SupabaseStore(
                        supabase_url,
                        supabase_key,
                        session["access_token"],
                        session["refresh_token"],
                    )
                    extraction_items = st.session_state.get(
                        "document_extractions", []
                    )
                    document_ids = []
                    for index, uploaded in enumerate(uploaded_documents or []):
                        extraction_payload = (
                            extraction_items[index]
                            if index < len(extraction_items)
                            else {}
                        )
                        saved = store.save_document(
                            filename=uploaded.name,
                            mime_type=uploaded.type,
                            content=uploaded.getvalue(),
                            extraction=extraction_payload,
                        )
                        document_ids.append(saved.document_id)
                    status = (
                        "exception_available"
                        if exception_available
                        else ("complete" if overall_complete else "open")
                    )
                    assessment_id = store.save_assessment(
                        {
                            "supplier_reference": supplier.supplier_id,
                            "supply_reference": supply.supply_id,
                            "status": status,
                            "ai_extraction_reviewed": extraction_reviewed,
                            "document_ids": document_ids,
                            "supplier": supplier_outcome.register_row(),
                            "supply": supply_outcome.register_row(),
                        }
                    )
                    st.success(f"Assessment saved securely: {assessment_id}")
                except Exception as exc:
                    st.error(f"Assessment could not be saved: {exc}")
            if st.button("Sign out"):
                del st.session_state["supabase_session"]
                st.rerun()
    with st.expander("Preview readable assessment"):
        st.markdown(report)

with st.expander("Interpretation and privacy notes"):
    st.markdown(
        """
        - Threshold comparisons follow the Decision's strict wording: below
          AED 10,000 and exceeds AED 100,000 / AED 375,000.
        - The lookback is implemented as twelve calendar months.
        - AI document reading supports Arabic and English. It proposes fields;
          a person must review them before relying on the assessment.
        - Uploaded files are retained only when an authenticated user explicitly
          saves them to the configured private Supabase workspace.
        - Uploaded documents may contain legal names and TRNs. Extracted names,
          invoice references and related source fields may appear in the generated
          PDF, but the app does not retain them unless the user signs in and saves.
        - Forward expected spend can trigger checks before historic spend
          reaches a threshold.
        - The implementation was reconciled to the authoritative Arabic
          Decision and uses the FTA's unofficial English translation for
          English-language labels. If any discrepancy arises, the Arabic text prevails.
        """
    )
