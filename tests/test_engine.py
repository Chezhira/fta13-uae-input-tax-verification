from datetime import date, timedelta
from decimal import Decimal

import pytest

from fta13 import thresholds as T
from fta13.engine import evaluate_supplier, evaluate_supply
from fta13.models import (
    CheckKind, Evidence, HumanConclusion, PaymentMethod, PersonType,
    RiskEvent, Supplier, Supply, Verdict,
)

AS_OF = date(2026, 12, 1)
D = Decimal


def supply(day: date, amount: str, sid="S1", **kw) -> Supply:
    return Supply(sid, "SUP1", day, D(amount), **kw)


def legal_supplier(**kw) -> Supplier:
    base = dict(
        supplier_id="SUP1",
        legal_name="Acme Trading LLC",
        person_type=PersonType.LEGAL,
        licensed_activities=["general trading"],
        verified_on=AS_OF - timedelta(days=30),
    )
    base.update(kw)
    return Supplier(**base)


# --- Article 6 boundaries ---------------------------------------------------

@pytest.mark.parametrize(
    "amount,expected_required",
    [("9999.99", False), ("10000", True), ("10000.01", True)],
)
def test_de_minimis_is_strictly_below_10k(amount, expected_required):
    a = T.assess(as_of=AS_OF, prior_supplies=[], consideration_ex_vat=D(amount))
    assert a.verification_required is expected_required


def test_100k_ceiling_is_strictly_exceeds():
    at = [supply(AS_OF - timedelta(days=10), "100000")]
    over = [supply(AS_OF - timedelta(days=10), "100000.01")]
    assert not T.assess(
        as_of=AS_OF, prior_supplies=at, consideration_ex_vat=D("500")
    ).de_minimis_withdrawn
    assert T.assess(
        as_of=AS_OF, prior_supplies=over, consideration_ex_vat=D("500")
    ).de_minimis_withdrawn


def test_small_supply_in_scope_once_supplier_crosses_100k():
    prior = [supply(AS_OF - timedelta(days=d), "30000", f"S{d}") for d in (300, 200, 100, 50)]
    a = T.assess(as_of=AS_OF, prior_supplies=prior, consideration_ex_vat=D("250"))
    assert a.trailing_12m == D("120000")
    assert a.de_minimis_available is True      # the supply itself is under 10k
    assert a.de_minimis_withdrawn is True      # but Art 6(2) removes the relief
    assert a.verification_required is True


def test_forward_expectation_alone_withdraws_de_minimis():
    a = T.assess(
        as_of=AS_OF,
        prior_supplies=[],
        expected_forward_12m=D("150000"),
        consideration_ex_vat=D("400"),
    )
    assert a.trailing_12m == 0
    assert a.verification_required is True


# --- Article 3(4) -----------------------------------------------------------

def test_enhanced_checks_trigger_on_forward_expectation_at_onboarding():
    s = legal_supplier(expected_forward_12m=D("400000"), verified_on=None)
    out = evaluate_supplier(s, as_of=AS_OF, prior_supplies=[])
    assert out.assessment.enhanced_checks_required
    ids = {r.clause_id for r in out.results if r.verdict is not Verdict.NOT_APPLICABLE}
    assert "3.4.a" in ids and "3.4.b" in ids


def test_enhanced_checks_not_applicable_below_trigger():
    s = legal_supplier(expected_forward_12m=D("375000"))   # equal, not exceeding
    out = evaluate_supplier(s, as_of=AS_OF, prior_supplies=[])
    assert not out.assessment.enhanced_checks_required
    res = {r.clause_id: r.verdict for r in out.results}
    assert res["3.4.a"] is Verdict.NOT_APPLICABLE


# --- rolling window ---------------------------------------------------------

def test_window_rolls_and_drops_old_supplies():
    old = supply(AS_OF - timedelta(days=400), "500000", "OLD")
    recent = supply(AS_OF - timedelta(days=10), "5000", "NEW")
    a = T.assess(as_of=AS_OF, prior_supplies=[old, recent], consideration_ex_vat=D("100"))
    assert a.trailing_12m == D("5000")
    assert not a.de_minimis_withdrawn


# --- Article 3(3) risk indicators ------------------------------------------

def test_three_address_changes_block_completion_without_justification():
    events = [
        RiskEvent("address_change", AS_OF - timedelta(days=d)) for d in (300, 200, 100)
    ]
    s = legal_supplier(risk_events=events)
    out = evaluate_supplier(s, as_of=AS_OF)
    r = next(r for r in out.results if r.clause_id == "3.3.a.1")
    assert r.verdict is Verdict.FAILED
    assert r.blocking is True
    assert "3.3.a.1" in {g.clause_id for g in out.blocking_gaps}


def test_exactly_two_changes_does_not_trigger():
    events = [
        RiskEvent("address_change", AS_OF - timedelta(days=d)) for d in (300, 100)
    ]
    out = evaluate_supplier(legal_supplier(risk_events=events), as_of=AS_OF)
    r = next(r for r in out.results if r.clause_id == "3.3.a.1")
    assert r.verdict is Verdict.SATISFIED


def test_justification_clears_a_triggered_indicator():
    events = [
        RiskEvent("key_employee_change", AS_OF - timedelta(days=d)) for d in (300, 200, 50)
    ]
    s = legal_supplier(
        risk_events=events,
        risk_justifications={"3.3.a.2": "Post-acquisition restructuring, board minute REF-88."},
    )
    out = evaluate_supplier(s, as_of=AS_OF)
    r = next(r for r in out.results if r.clause_id == "3.3.a.2")
    assert r.verdict is Verdict.SATISFIED


# --- Article 5(1) re-verification ------------------------------------------

def test_stale_verification_blocks_the_supply():
    s = legal_supplier(verified_on=AS_OF - timedelta(days=400))
    out = evaluate_supply(s, supply(AS_OF, "50000"), prior_supplies=[])
    assert any(g.clause_id == "5.1" for g in out.blocking_gaps)
    assert out.decision_13_verification_complete is False


def test_first_dealing_flags_verification_needed():
    out = evaluate_supplier(legal_supplier(verified_on=None), as_of=AS_OF)
    assert any("no prior verification" in w for w in out.warnings)
    assert any(g.clause_id == "5.1" for g in out.blocking_gaps)


# --- evidence-driven document clauses --------------------------------------

def test_expired_id_is_not_satisfied():
    s = legal_supplier(
        evidence=[
            Evidence("representative_id", "DMS/1", AS_OF - timedelta(days=60),
                     expires_on=AS_OF - timedelta(days=5))
        ]
    )
    out = evaluate_supplier(s, as_of=AS_OF)
    r = next(r for r in out.results if r.clause_id == "3.1.b.2")
    assert r.verdict is Verdict.MISSING
    assert "not valid" in r.detail


def test_natural_person_clauses_apply_only_to_natural_persons():
    out = evaluate_supplier(legal_supplier(), as_of=AS_OF)
    res = {r.clause_id: r.verdict for r in out.results}
    assert res["3.1.a.1"] is Verdict.NOT_APPLICABLE
    assert res["3.1.b.1"] is Verdict.MISSING


# --- conditional supply clauses --------------------------------------------

def test_offshore_payment_adds_a_clause():
    sp = supply(AS_OF, "50000", payee_country="KY")
    out = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    res = {r.clause_id: r.verdict for r in out.results}
    assert res["4.2.a.offshore"] is Verdict.MISSING


def test_same_country_payment_does_not():
    sp = supply(AS_OF, "50000", payee_country="AE")
    out = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    res = {r.clause_id: r.verdict for r in out.results}
    assert res["4.2.a.offshore"] is Verdict.NOT_APPLICABLE


def test_services_skip_the_origin_and_title_clause():
    sp = supply(AS_OF, "50000", is_goods=False)
    out = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    res = {r.clause_id: r.verdict for r in out.results}
    assert res["4.3.c.origin"] is Verdict.NOT_APPLICABLE
    assert res["4.3.c.title"] is Verdict.NOT_APPLICABLE


def test_cash_payment_requires_documented_rationale():
    sp = supply(AS_OF, "50000", payment_method=PaymentMethod.CASH)
    out = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    res = {r.clause_id: r.verdict for r in out.results}
    assert res["4.2.b.cash"] is Verdict.MISSING


# --- judgment clauses need a signature -------------------------------------

def test_judgment_clause_requires_named_signoff():
    sp = supply(AS_OF, "50000")
    out = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    assert "4.1.b" in {r.clause_id for r in out.open_judgments}

    sp.conclusions.append(
        HumanConclusion("4.1.b", True, "Recurring input for line 3.", "A. Reviewer", AS_OF)
    )
    out2 = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    assert "4.1.b" not in {r.clause_id for r in out2.open_judgments}


def test_negative_conclusion_blocks_the_claim():
    sp = supply(AS_OF, "50000", is_goods=False)
    sp.conclusions.append(
        HumanConclusion("4.3.b", False, "Outside licensed activity.", "A. Reviewer", AS_OF)
    )
    out = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    assert out.decision_13_verification_complete is False


def test_goods_require_both_origin_and_title_evidence():
    sp = supply(
        AS_OF,
        "50000",
        evidence=[Evidence("origin_document", "DMS/ORIGIN", AS_OF)],
    )
    out = evaluate_supply(legal_supplier(), sp, prior_supplies=[])
    res = {r.clause_id: r.verdict for r in out.results}
    assert res["4.3.c.origin"] is Verdict.SATISFIED
    assert res["4.3.c.title"] is Verdict.MISSING


def test_mixed_supplier_history_is_rejected():
    mixed = [Supply("X", "OTHER", AS_OF, D("50000"))]
    with pytest.raises(ValueError, match="other suppliers"):
        evaluate_supply(legal_supplier(), supply(AS_OF, "50000"), prior_supplies=mixed)


def test_negative_amounts_are_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        supply(AS_OF, "-1")


# --- retrospection flag -----------------------------------------------------

def test_retrospective_exposure_surfaces_earlier_skipped_supplies():
    prior = [supply(AS_OF - timedelta(days=d), "9000", f"S{d}") for d in range(340, 20, -20)]
    exposure = T.retrospective_exposure(prior)
    assert exposure, "expected supplies taken under the de minimis before crossing"
    assert all(s.consideration_ex_vat < T.DE_MINIMIS_PER_SUPPLY for s in exposure)


# --- the AI layer cannot touch deterministic clauses -----------------------

def test_ai_layer_refuses_computed_and_document_clauses():
    from fta13 import ai

    with pytest.raises(ValueError):
        ai.draft("3.3.a.1", {})       # COMPUTED
    with pytest.raises(ValueError):
        ai.draft("3.4.a", {})         # DOCUMENT
    with pytest.raises(KeyError):
        ai.draft("9.9.9", {})


def test_accepting_a_draft_requires_a_named_human():
    from datetime import datetime, timezone
    from fta13.ai import AIDraft

    d = AIDraft("id1", "4.3.b", "m", datetime.now(timezone.utc), "abc", True, "high", "looks fine")
    with pytest.raises(ValueError):
        d.accept(decided_by="  ", decided_on=AS_OF, conclusion=True)

    c = d.accept(decided_by="A. Reviewer", decided_on=AS_OF, conclusion=False)
    assert c.conclusion is False          # the human overrode the model
    assert c.ai_draft_id == "id1"
