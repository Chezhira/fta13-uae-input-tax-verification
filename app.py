"""Bilingual, document-assisted workflow for FTA Decision No. 13 of 2026."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

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
    sha256_bytes,
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
from fta13.linkage import DocumentLinkage, link_document_to_supplier
from fta13.portfolio import (
    LedgerTransaction,
    PortfolioValidationError,
    parse_ledger_rows,
    screen_portfolio,
)
from fta13.reporting import build_pdf_report, evidence_strength_summary


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

st.markdown(
    """
    <style>
    :root { --navy:#17324d; --teal:#087f73; --teal-dark:#06665d; --ink:#243447; --muted:#617184; --line:#dce5ec; --canvas:#f4f7fa; }
    [data-testid="stAppViewContainer"] { background:radial-gradient(circle at 92% 2%,rgba(8,127,115,.09),transparent 23rem),var(--canvas); color:var(--ink); }
    [data-testid="stHeader"] { background:rgba(244,247,250,.88); }
    .block-container { max-width:1260px; padding-top:2.1rem; padding-bottom:4rem; }
    h1,h2,h3 { color:var(--navy); letter-spacing:-.02em; }
    p,label { line-height:1.55; }
    .fta-hero { position:relative; overflow:hidden; padding:2.15rem 2.25rem 1.9rem; margin-bottom:1rem; border:1px solid rgba(255,255,255,.14); border-radius:20px; background:linear-gradient(125deg,#142f49 0%,#174b58 62%,#087f73 100%); box-shadow:0 18px 45px rgba(20,47,73,.15); color:white; }
    .fta-hero::after { content:""; position:absolute; width:15rem; height:15rem; right:-5rem; top:-7rem; border:1px solid rgba(255,255,255,.2); border-radius:50%; box-shadow:0 0 0 2.5rem rgba(255,255,255,.035); }
    .fta-eyebrow { color:#a7e2d8; font-size:.76rem; font-weight:750; letter-spacing:.14em; text-transform:uppercase; }
    .fta-hero h1 { max-width:860px; margin:.4rem 0 .55rem; color:white; font-size:clamp(2rem,4vw,3.1rem); line-height:1.08; }
    .fta-hero p { max-width:780px; margin:0; color:#dcecf0; font-size:1.03rem; }
    .fta-chips { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:1.2rem; }
    .fta-chip { padding:.34rem .68rem; border:1px solid rgba(255,255,255,.2); border-radius:999px; background:rgba(255,255,255,.09); color:#f5ffff; font-size:.78rem; font-weight:650; }
    .fta-workflow { display:grid; grid-template-columns:repeat(4,1fr); gap:.7rem; margin:1rem 0 1.3rem; }
    .fta-step { padding:.85rem 1rem; border:1px solid var(--line); border-radius:12px; background:rgba(255,255,255,.82); color:var(--muted); font-size:.82rem; }
    .fta-step b { display:block; margin-bottom:.15rem; color:var(--navy); font-size:.9rem; }
    .fta-section { margin:2rem 0 .75rem; padding-left:.9rem; border-left:4px solid var(--teal); }
    .fta-section-kicker { color:var(--teal); font-size:.72rem; font-weight:750; letter-spacing:.1em; text-transform:uppercase; }
    .fta-section-title { margin:.1rem 0; color:var(--navy); font-size:1.4rem; font-weight:740; }
    .fta-section-copy { color:var(--muted); font-size:.91rem; }
    [data-testid="stMetric"] { min-height:116px; padding:1rem 1.1rem; border:1px solid var(--line); border-radius:14px; background:white; box-shadow:0 6px 18px rgba(23,50,77,.05); }
    [data-testid="stMetricLabel"] { color:var(--muted); }
    [data-testid="stMetricValue"] { color:var(--navy); font-weight:750; }
    [data-testid="stExpander"] { border:1px solid var(--line); border-radius:14px; background:rgba(255,255,255,.9); box-shadow:0 5px 16px rgba(23,50,77,.035); overflow:hidden; }
    [data-testid="stFileUploader"] section { border:1.5px dashed #8bbdb7; border-radius:14px; background:#f6fbfa; }
    [data-baseweb="tab-list"] { gap:.35rem; padding:.3rem; border:1px solid var(--line); border-radius:13px; background:#eaf0f4; }
    [data-baseweb="tab"] { height:2.75rem; padding:0 1rem; border-radius:9px; color:var(--muted); font-weight:650; }
    [data-baseweb="tab"][aria-selected="true"] { background:white; color:var(--teal); box-shadow:0 3px 10px rgba(23,50,77,.08); }
    [data-baseweb="tab-highlight"] { display:none; }
    div.stButton>button,div.stDownloadButton>button { border:1px solid var(--teal); border-radius:9px; font-weight:680; transition:transform .15s ease,box-shadow .15s ease; }
    div.stButton>button:hover,div.stDownloadButton>button:hover { border-color:var(--teal-dark); color:var(--teal-dark); box-shadow:0 5px 14px rgba(8,127,115,.14); transform:translateY(-1px); }
    div.stButton>button[kind="primary"] { background:var(--teal); color:white; }
    [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:12px; overflow:hidden; }
    input:focus,textarea:focus { border-color:var(--teal)!important; }
    @media (max-width:760px) { .block-container{padding-top:1rem}.fta-hero{padding:1.5rem 1.25rem;border-radius:15px}.fta-workflow{grid-template-columns:repeat(2,1fr)}[data-baseweb="tab"]{padding:0 .55rem;font-size:.82rem} }
    </style>
    <section class="fta-hero">
      <div class="fta-eyebrow">UAE VAT control tool</div>
      <h1>FTA Decision 13 Readiness &amp; Verification</h1>
      <p>Screen supplier exposure, connect supporting documents and produce a review-ready verification record.</p>
      <div class="fta-chips"><span class="fta-chip">Portfolio screening</span><span class="fta-chip">Arabic + English documents</span><span class="fta-chip">Human-controlled AI</span></div>
    </section>
    <div class="fta-workflow"><div class="fta-step"><b>1 · Screen</b>Rank supplier exposure</div><div class="fta-step"><b>2 · Link</b>Match source documents</div><div class="fta-step"><b>3 · Assess</b>Complete human checks</div><div class="fta-step"><b>4 · Export</b>Retain the record</div></div>
    """,
    unsafe_allow_html=True,
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


def section_header(kicker: str, title: str, copy: str) -> None:
    """Render a consistent heading from static application text."""
    st.markdown(
        f'<div class="fta-section"><div class="fta-section-kicker">{kicker}</div>'
        f'<div class="fta-section-title">{title}</div>'
        f'<div class="fta-section-copy">{copy}</div></div>',
        unsafe_allow_html=True,
    )


RULESET_LABEL = setting("FTA13_RULESET", f"fta13 {ENGINE_VERSION}")

SESSION_DEFAULTS = {
    "assessment_date_input": date(2026, 10, 1),
    "supplier_ref_input": "SUPPLIER-001",
    "supply_ref_input": "SUPPLY-001",
    "country_input": "AE",
    "invoice_value_input": 2400.0,
    "trailing_spend_input": 120000.0,
    "forward_spend_input": 150000.0,
    "verified_on_input": None,
    "payment_method_input": "Electronic",
    "is_goods_input": True,
    "offshore_input": False,
}
for session_key, default_value in SESSION_DEFAULTS.items():
    st.session_state.setdefault(session_key, default_value)


def apply_extraction(item: DocumentExtraction) -> None:
    """Prefill only supported values; the visible widgets remain human-editable."""
    mapping = {
        "supply_ref_input": item.invoice_number.normalized or item.invoice_number.original,
        "country_input": item.country_of_incorporation.normalized,
        "description_input": item.supply_description.original,
    }
    if not st.session_state.get("portfolio_linkage_context"):
        mapping["supplier_ref_input"] = (
            item.supplier_reference.normalized or item.supplier_reference.original
        )
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
    st.session_state["document_linkage_confirmed"] = False


def money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def evidence(kind: str, held: bool, as_of: date) -> list[Evidence]:
    if not held:
        return []
    matching = [
        item
        for item in st.session_state.get("document_evidence_records", [])
        if kind in item.get("evidence_kinds", [])
        and item.get("sha256")
        and item.get("filename")
        and item.get("sha256")
        in st.session_state.get("active_document_hashes", set())
    ]
    if matching:
        return [
            Evidence(
                kind=kind,
                reference=f"uploaded:{item['filename']}",
                obtained_on=as_of,
                sha256=item["sha256"],
                note=(
                    "AI proposed this document type; a person confirmed the "
                    "document against the requirement."
                ),
            )
            for item in matching
        ]
    return [
        Evidence(
            kind=kind,
            reference=f"user-confirmed:{kind}",
            obtained_on=as_of,
            note="Visitor confirmed that valid evidence is held; document not uploaded.",
        )
    ]


def rows_to_csv(rows: list[dict[str, object]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def portfolio_transactions_for(supplier_reference: str) -> list[LedgerTransaction]:
    rows = st.session_state.get("portfolio_transaction_rows", [])
    return [
        LedgerTransaction(
            supplier_reference=str(row["supplier_reference"]),
            supplier_name=str(row.get("supplier_name", "")),
            supplier_trn=str(row.get("supplier_trn", "")),
            supply_reference=str(row["supply_reference"]),
            supply_date=date.fromisoformat(str(row["supply_date"])),
            amount_excluding_vat=Decimal(str(row["amount_excluding_vat"])),
            input_vat=Decimal(str(row.get("input_vat", "0") or "0")),
            expected_next_12m=Decimal(
                str(row.get("expected_next_12m", "0") or "0")
            ),
            last_verified_on=(
                date.fromisoformat(str(row["last_verified_on"]))
                if row.get("last_verified_on")
                else None
            ),
        )
        for row in rows
        if str(row.get("supplier_reference", "")) == supplier_reference
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
    document_linkage: dict[str, object] | None = None,
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
        "linked_supplier_reference",
        "linked_supply_reference",
        "document_linkage_status",
        "document_match_basis",
        "linked_document_hashes",
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
                    "linked_supplier_reference": (
                        document_linkage.get("supplier_reference", "")
                        if document_linkage
                        else ""
                    ),
                    "linked_supply_reference": (
                        document_linkage.get("matched_supply_reference", "")
                        if document_linkage
                        else ""
                    ),
                    "document_linkage_status": (
                        "human_confirmed" if document_linkage else ""
                    ),
                    "document_match_basis": (
                        document_linkage.get("match_basis", "")
                        if document_linkage
                        else ""
                    ),
                    "linked_document_hashes": (
                        "; ".join(
                            str(item.get("sha256", ""))
                            for item in document_linkage.get("documents", [])
                        )
                        if document_linkage
                        else ""
                    ),
                }
            )
    return buffer.getvalue()


def make_markdown_report(
    supplier_outcome: VerificationOutcome,
    supply_outcome: VerificationOutcome,
    *,
    reviewer: str,
    generated_on_utc: str,
    evidence_strength: dict[str, int] | None = None,
    document_linkage: dict[str, object] | None = None,
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
    if evidence_strength is not None:
        lines.extend(
            [
                "",
                "## Evidence strength",
                "",
                f"- Uploaded and hashed (human-confirmed): {evidence_strength['uploaded_and_hashed']}",
                f"- Confirmed as held, but not uploaded: {evidence_strength['self_attested']}",
                f"- Missing applicable document requirements: {evidence_strength['missing_document_requirements']}",
            ]
        )
    if document_linkage is not None:
        lines.extend(
            [
                "",
                "## Ledger-to-document linkage",
                "",
                f"- Internal supplier reference: {document_linkage.get('supplier_reference', '')}",
                f"- Supplier name from ledger: {document_linkage.get('supplier_name', '') or 'not provided'}",
                f"- Supplier TRN from ledger: {document_linkage.get('supplier_trn', '') or 'not provided'}",
                f"- Matched supply reference: {document_linkage.get('matched_supply_reference', '')}",
                f"- Match basis: {document_linkage.get('match_basis', '')}",
                f"- Confirmed by: {document_linkage.get('confirmed_by', '')}",
                f"- Linked document hashes: {', '.join(str(item.get('sha256', '')) for item in document_linkage.get('documents', []))}",
            ]
        )
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


PORTFOLIO_TEMPLATE = rows_to_csv(
    [
        {
            "supplier_reference": "SUP-001",
            "supplier_name": "ABC Trading LLC",
            "supplier_trn": "100123456700003",
            "supply_reference": "INV-1001",
            "supply_date": "2026-07-15",
            "amount_excluding_vat": "8500.00",
            "input_vat": "425.00",
            "expected_next_12m": "120000.00",
            "last_verified_on": "2026-01-20",
        },
        {
            "supplier_reference": "SUP-001",
            "supplier_name": "ABC Trading LLC",
            "supplier_trn": "100123456700003",
            "supply_reference": "INV-1002",
            "supply_date": "2026-08-15",
            "amount_excluding_vat": "9500.00",
            "input_vat": "475.00",
            "expected_next_12m": "120000.00",
            "last_verified_on": "2026-01-20",
        },
    ]
)

section_header(
    "Step 1 · Screen",
    "Prioritise the supplier portfolio",
    "Upload an AP ledger to rank exposure before opening an individual assessment.",
)
with st.expander("Portfolio screening from an AP ledger", expanded=True):
    st.caption(
        "Upload a CSV to rank suppliers for review before opening an individual "
        "assessment. Processing is session-only: the ledger is not saved by this "
        "screen. Amounts must be in AED and dates must use YYYY-MM-DD."
    )
    st.download_button(
        "Download ledger template (.csv)",
        data=PORTFOLIO_TEMPLATE,
        file_name="fta13_portfolio_template.csv",
        mime="text/csv",
        disabled=False,
    )
    ledger_upload = st.file_uploader(
        "AP ledger CSV",
        type=["csv"],
        key="portfolio_ledger_upload",
        help="Use transaction-level rows. Supplier forecasts and verification dates may repeat.",
    )
    portfolio_rows: list[dict[str, object]] = []
    if ledger_upload is not None:
        try:
            if ledger_upload.size > 10 * 1024 * 1024:
                raise PortfolioValidationError("The ledger must be 10 MB or smaller.")
            decoded = ledger_upload.getvalue().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(decoded))
            headers = reader.fieldnames or []
            raw_rows = list(reader)
            if not headers:
                raise PortfolioValidationError("The CSV has no header row.")
            st.markdown("**Map your columns**")
            required_fields = {
                "supplier_reference": "Supplier reference",
                "supply_reference": "Supply / invoice reference",
                "supply_date": "Supply date",
                "amount_excluding_vat": "Amount excluding VAT (AED)",
            }
            optional_fields = {
                "supplier_name": "Supplier legal name (recommended for document linkage)",
                "supplier_trn": "Supplier TRN (recommended for document linkage)",
                "input_vat": "Input VAT (optional)",
                "expected_next_12m": "Expected next 12 months (optional)",
                "last_verified_on": "Last verified on (optional)",
            }
            column_map: dict[str, str | None] = {}
            map_columns = st.columns(2)
            for index, (field, label) in enumerate(required_fields.items()):
                default_index = headers.index(field) if field in headers else 0
                column_map[field] = map_columns[index % 2].selectbox(
                    label,
                    headers,
                    index=default_index,
                    key=f"portfolio_map_{field}",
                )
            choices = ["Not provided", *headers]
            for index, (field, label) in enumerate(optional_fields.items()):
                default_index = choices.index(field) if field in headers else 0
                selected = map_columns[index % 2].selectbox(
                    label,
                    choices,
                    index=default_index,
                    key=f"portfolio_map_{field}",
                )
                column_map[field] = None if selected == "Not provided" else selected
            portfolio_as_of = st.date_input(
                "Portfolio as-of date",
                value=datetime.now(ZoneInfo("Asia/Dubai")).date(),
                key="portfolio_as_of_date",
            )
            if st.button("Screen supplier portfolio", width="stretch"):
                transactions = parse_ledger_rows(raw_rows, column_map)
                results = screen_portfolio(transactions, as_of=portfolio_as_of)
                st.session_state["portfolio_screen_rows"] = [
                    item.as_row() for item in results
                ]
                st.session_state["portfolio_transaction_rows"] = [
                    item.as_row() for item in transactions
                ]
        except (UnicodeDecodeError, csv.Error, PortfolioValidationError) as exc:
            st.error(f"Portfolio screening could not run: {exc}")
    portfolio_rows = st.session_state.get("portfolio_screen_rows", [])
    if portfolio_rows:
        enhanced = sum(row["enhanced_checks_required"] is True for row in portfolio_rows)
        full = sum(
            str(row["priority"]).startswith(("1", "2")) for row in portfolio_rows
        )
        exposure = sum(
            (Decimal(str(row["input_vat_screening_exposure_aed"])) for row in portfolio_rows),
            Decimal("0"),
        )
        p1, p2, p3 = st.columns(3)
        p1.metric("Suppliers screened", len(portfolio_rows))
        p2.metric("Full / enhanced priority", full)
        p3.metric("Input VAT screening exposure", f"AED {exposure:,.2f}")
        if enhanced:
            st.warning(f"{enhanced} supplier(s) trigger the AED 375,000 enhanced-check threshold.")
        st.dataframe(portfolio_rows, hide_index=True, width="stretch")
        labels = {
            row["supplier_reference"]: (
                f"{row['supplier_reference']} · {row['supplier_name']}"
                if row.get("supplier_name")
                else str(row["supplier_reference"])
            )
            for row in portfolio_rows
        }
        selected_supplier = st.selectbox(
            "Supplier to connect to uploaded documents",
            ["No linked assessment", *labels],
            format_func=lambda value: labels.get(value, value),
            key="selected_portfolio_supplier",
            help=(
                "The internal supplier reference groups the AP ledger. The app "
                "will separately match document name, TRN and invoice details."
            ),
        )
        selected_supplier = (
            "" if selected_supplier == "No linked assessment" else selected_supplier
        )
        if selected_supplier != st.session_state.get("portfolio_linkage_context", ""):
            st.session_state["portfolio_linkage_context"] = selected_supplier
            st.session_state["document_linkage_confirmed"] = False
            st.session_state.pop("confirmed_document_linkage", None)
            if selected_supplier:
                st.session_state["supplier_ref_input"] = selected_supplier
                selected_row = next(
                    row
                    for row in portfolio_rows
                    if row["supplier_reference"] == selected_supplier
                )
                st.session_state["trailing_spend_input"] = float(
                    selected_row["trailing_12m_aed"]
                )
                st.session_state["forward_spend_input"] = float(
                    selected_row["expected_next_12m_aed"]
                )
                st.session_state["verified_on_input"] = (
                    date.fromisoformat(str(selected_row["last_verified_on"]))
                    if selected_row.get("last_verified_on")
                    else None
                )
            st.rerun()
    portfolio_csv = rows_to_csv(portfolio_rows)
    st.download_button(
        "Download ranked screening register (.csv)",
        data=portfolio_csv,
        file_name="fta13_portfolio_screening.csv",
        mime="text/csv",
        disabled=not bool(portfolio_rows),
    )


section_header(
    "Step 2 · Link",
    "Document assistant | مساعد المستندات",
    "Upload up to five Arabic or English documents for one supplier and invoice set. You remain responsible for every conclusion.",
)
uploaded_documents = st.file_uploader(
    "Documents for one supplier and one supply",
    type=["pdf", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
    help="Up to five documents, maximum 5 MB each.",
)
st.session_state["active_document_hashes"] = {
    sha256_bytes(uploaded.getvalue()) for uploaded in (uploaded_documents or [])
}
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
                st.session_state["document_evidence_records"] = [
                    {
                        "filename": uploaded.name,
                        "sha256": sha256_bytes(uploaded.getvalue()),
                        "evidence_kinds": item.evidence_kinds,
                    }
                    for uploaded, item in zip(uploaded_documents, extractions)
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

selected_portfolio_supplier = st.session_state.get("portfolio_linkage_context", "")
document_linkage: DocumentLinkage | None = None
linkage_confirmed = not bool(selected_portfolio_supplier)
if selected_portfolio_supplier:
    st.subheader("Ledger-to-document linkage")
    st.caption(
        f"Selected internal supplier reference: {selected_portfolio_supplier}. "
        "The AI cannot infer this ERP identifier; the fields below connect the "
        "uploaded document to that selected ledger supplier."
    )
    selected_ledger_rows = portfolio_transactions_for(selected_portfolio_supplier)
    if merged_extraction is None:
        st.info(
            "Upload and read a supplier invoice or supporting document to create "
            "the linkage proposal."
        )
        linkage_confirmed = False
        st.session_state.pop("confirmed_document_linkage", None)
    else:
        try:
            document_linkage = link_document_to_supplier(
                merged_extraction, selected_ledger_rows
            )
            st.dataframe(
                [
                    {
                        "Field": row.field,
                        "AP ledger": row.ledger_value or "—",
                        "Uploaded document": row.document_value or "—",
                        "Result": row.result,
                    }
                    for row in document_linkage.comparisons
                ],
                hide_index=True,
                width="stretch",
            )
            st.write(f"**Matching basis:** {document_linkage.match_basis}")
            for conflict in document_linkage.conflicts:
                st.error(conflict)
            for warning in document_linkage.warnings:
                st.warning(warning)
            linkage_confirmed = st.checkbox(
                "I confirm that the uploaded documents belong to the selected "
                "ledger supplier and matched transaction.",
                key="document_linkage_confirmed",
                disabled=not document_linkage.can_confirm,
            )
            if linkage_confirmed and document_linkage.matched_supply_reference:
                st.session_state["supplier_ref_input"] = selected_portfolio_supplier
                st.session_state["supply_ref_input"] = (
                    document_linkage.matched_supply_reference
                )
                confirmed = document_linkage.as_dict()
                confirmed["human_confirmed"] = True
                st.session_state["confirmed_document_linkage"] = confirmed
            else:
                st.session_state.pop("confirmed_document_linkage", None)
        except ValueError as exc:
            st.error(f"Document linkage could not be prepared: {exc}")
            linkage_confirmed = False
            st.session_state.pop("confirmed_document_linkage", None)


section_header(
    "Steps 3–4 · Assess and export",
    "Complete the verification record",
    "Review the scenario, confirm the applicable checks and export the retained evidence trail.",
)
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
            step=1000.0,
            key="trailing_spend_input",
        )
        forward_spend = st.number_input(
            "Expected next 12-month spend (AED)",
            min_value=0.0,
            step=1000.0,
            key="forward_spend_input",
        )
        verified_on = st.date_input(
            "Last completed supplier verification",
            max_value=assessment_date,
            key="verified_on_input",
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
    linkage_record = None
    if selected_portfolio_supplier and linkage_confirmed and document_linkage:
        linkage_record = document_linkage.as_dict()
        linkage_record["human_confirmed"] = True
        linkage_record["confirmed_by"] = reviewer.strip()
        linkage_record["confirmed_on"] = assessment_date.isoformat()
        linkage_record["documents"] = [
            {
                "filename": item.get("filename", ""),
                "sha256": item.get("sha256", ""),
                "evidence_kinds": item.get("evidence_kinds", []),
            }
            for item in st.session_state.get("document_evidence_records", [])
            if item.get("sha256")
            in st.session_state.get("active_document_hashes", set())
        ]
    evidence_strength = evidence_strength_summary(
        supplier_outcome,
        supply_outcome,
        supplier.evidence,
        supply.evidence,
    )
    st.subheader("Evidence strength")
    e1, e2, e3 = st.columns(3)
    e1.metric("Uploaded and hashed", evidence_strength["uploaded_and_hashed"])
    e2.metric("Self-attested as held", evidence_strength["self_attested"])
    e3.metric(
        "Missing document requirements",
        evidence_strength["missing_document_requirements"],
    )
    st.caption(
        "Uploaded evidence counts only after a person confirms the matching "
        "checkbox. AI suggestions alone never satisfy a requirement."
    )
    report = make_markdown_report(
        supplier_outcome,
        supply_outcome,
        reviewer=reviewer,
        generated_on_utc=generated_on_utc,
        evidence_strength=evidence_strength,
        document_linkage=linkage_record,
    )
    register_csv = make_register_csv(
        supplier_outcome, supply_outcome, document_linkage=linkage_record
    )
    audit_json = json.dumps(
        {
            "supplier": supplier_outcome.register_row(),
            "supply": supply_outcome.register_row(),
            "ruleset_version": RULESET_LABEL,
            "generated_on_utc": generated_on_utc,
            "reviewer": reviewer or "not stated",
            "reviewer_identity_verified": False,
            "evidence_strength": evidence_strength,
            "document_linkage": linkage_record,
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
        evidence_strength=evidence_strength,
        document_linkage=linkage_record,
    )

    linkage_exports_locked = bool(selected_portfolio_supplier) and (
        not linkage_confirmed or not reviewer.strip()
    )
    exports_locked = (
        (merged_extraction is not None and not extraction_reviewed)
        or linkage_exports_locked
    )
    if exports_locked:
        if merged_extraction is not None and not extraction_reviewed:
            st.error(
                "Confirm that you have reviewed the AI-populated fields before "
                "exporting a verification record."
            )
        if linkage_exports_locked:
            st.error(
                "Confirm the ledger-to-document linkage and enter the reviewer "
                "name before exporting this portfolio-linked assessment."
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
                            "document_linkage": linkage_record,
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
