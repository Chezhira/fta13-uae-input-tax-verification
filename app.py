"""Public, no-upload scenario tester for FTA Decision No. 13 of 2026."""

from datetime import date
from decimal import Decimal

import streamlit as st

from fta13.engine import evaluate_supply
from fta13.models import PaymentMethod, PersonType, Supplier, Supply, Verdict


st.set_page_config(page_title="FTA13 Verification Tester", page_icon="✓", layout="wide")
st.title("FTA Decision 13 Verification Tester")
st.caption("Explore which verification measures apply from 1 October 2026.")
st.warning(
    "Educational scenario tool only. It assesses Decision No. 13 verification "
    "measures, not overall input-tax recoverability. Do not enter confidential data."
)

with st.sidebar:
    st.header("Scenario")
    assessment_date = st.date_input("Supply date", value=date(2026, 10, 1))
    invoice_value = st.number_input(
        "This supply, excluding VAT (AED)", min_value=0.0, value=2400.0, step=100.0
    )
    trailing_spend = st.number_input(
        "Prior 12-month supplier spend (AED)", min_value=0.0, value=120000.0, step=1000.0
    )
    forward_spend = st.number_input(
        "Expected next 12-month spend (AED)", min_value=0.0, value=150000.0, step=1000.0
    )
    person_type = st.selectbox("Supplier type", ["Legal person", "Natural person"])
    is_goods = st.toggle("Goods supply", value=True)
    payment = st.selectbox("Payment method", ["Electronic", "Cash"])
    third_party = st.toggle("Third party involved in payment")
    offshore = st.toggle("Payment account outside incorporation country")
    intermediary = st.toggle("Supplier acts as intermediary")

supplier = Supplier(
    supplier_id="SCENARIO-SUPPLIER",
    legal_name="Scenario supplier",
    person_type=PersonType.LEGAL if person_type == "Legal person" else PersonType.NATURAL,
    country_of_incorporation="AE",
    verified_on=None,
    expected_forward_12m=Decimal(str(forward_spend)),
)
current = Supply(
    supply_id="SCENARIO-SUPPLY",
    supplier_id=supplier.supplier_id,
    supply_date=assessment_date,
    consideration_ex_vat=Decimal(str(invoice_value)),
    payment_method=PaymentMethod.CASH if payment == "Cash" else PaymentMethod.ELECTRONIC,
    third_party_in_payment=third_party,
    payee_country="GB" if offshore else "AE",
    supplier_is_intermediary=intermediary,
    is_goods=is_goods,
)
history = []
if trailing_spend:
    history.append(
        Supply(
            supply_id="PRIOR-TOTAL",
            supplier_id=supplier.supplier_id,
            supply_date=assessment_date.replace(day=1),
            consideration_ex_vat=Decimal(str(trailing_spend)),
        )
    )

out = evaluate_supply(supplier, current, prior_supplies=history)
a = out.assessment

c1, c2, c3 = st.columns(3)
c1.metric("Decision 13 verification", "Required" if a.verification_required else "Exception available")
c2.metric("AED 100k exception ceiling", "Exceeded" if a.de_minimis_withdrawn else "Not exceeded")
c3.metric("Enhanced supplier checks", "Required" if a.enhanced_checks_required else "Not required")

st.subheader("Why")
for line in a.basis():
    st.write(f"• {line}")

if not a.verification_required:
    st.success("The Article 6 exception is available for this scenario. Continue monitoring supplier totals and expectations.")
else:
    applicable = [r for r in out.results if r.verdict is not Verdict.NOT_APPLICABLE]
    st.subheader(f"Applicable supply checks ({len(applicable)})")
    st.dataframe(
        [
            {
                "Article": r.article,
                "Requirement": r.requirement,
                "Evidence route": r.kind.value.title(),
            }
            for r in applicable
        ],
        hide_index=True,
        use_container_width=True,
    )
    st.info(
        "The public tester intentionally does not collect evidence or produce a "
        "completion verdict. The Python engine supports documented evidence and named human sign-off."
    )

with st.expander("Interpretation notes"):
    st.markdown(
        """
        - Thresholds use strict wording: below AED 10,000 and exceeds AED 100,000 / AED 375,000.
        - The lookback is implemented as twelve calendar months.
        - Forward expected spend can trigger checks before historic spend reaches a threshold.
        - Built from the unofficial English translation. Confirm against the Arabic text in the Official Gazette.
        """
    )
