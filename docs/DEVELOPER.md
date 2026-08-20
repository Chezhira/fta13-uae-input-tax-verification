# Developer Guide

`fta13` — verification engine for UAE Federal Tax Authority Decision No. 13 of 2026.

Audience: engineers extending this, integrating it into an ERP or AP workflow, or forking it for another jurisdiction. For the what-and-why, read `README.md` first.

---

## 1. The design constraint everything follows from

Article 5(3) of the Decision requires the taxable person to document verification steps and retain records **enabling the Authority to verify the correctness of their implementation**.

That single clause rules out a model-in-the-loop verdict. An FTA officer opening a file in 2029 must be able to recompute the outcome and get the same answer. A language model cannot offer that: it is not deterministic across versions, it has no accountable signatory, and its reasoning is not reproducible from the record.

So the system is split, and the split is enforced by types rather than by discipline.

```
structured facts ──► deterministic engine ──► verdict (reproducible, final)
                            ▲
                            │ HumanConclusion (named, dated, signed)
                            │
        evidence docs ──► AI advisory layer ──► AIDraft (advisory, inert)
```

An `AIDraft` cannot enter the verdict path. The only bridge is `AIDraft.accept()`, which requires a named individual and takes the conclusion as an explicit argument. There is no function anywhere that converts a model's view into a verdict.

---

## 2. Module map

| Module | Responsibility | Determinism |
|---|---|---|
| `models.py` | Dataclasses for the domain. No behaviour beyond `Evidence.is_valid_on()`. | Pure |
| `thresholds.py` | Arithmetic for Articles 3(3), 3(4), 5(1) and 6. No I/O, no imports beyond stdlib and `models`. | Pure |
| `clauses.py` | Registry of every operative requirement, as data. | Pure |
| `engine.py` | Applies the registry to a supplier or supply, returns a `VerificationOutcome`. | Pure |
| `ai.py` | Advisory drafting. The only module that does network I/O. | Non-deterministic by nature, inert by design |

Dependency direction is strictly one way: `ai` → `clauses` → `models`, and `engine` → `thresholds` → `models`. `thresholds` and `engine` never import `ai`. If that edge ever appears, the guarantee is broken.

---

## 3. Data model

### `Supplier`

| Field | Notes |
|---|---|
| `person_type` | `NATURAL` or `LEGAL`. Selects between the Article 3(1)(a) and 3(1)(b) clause sets. |
| `country_of_incorporation` | ISO-2. Compared against `Supply.payee_country` for the Article 4(2)(a) offshore limb. |
| `licensed_activities` | Free text from the trade licence. Consumed by the AI layer for clause 4.3.b, never by the engine. |
| `verified_on` | Date of last completed Article 3 verification. `None` means never. Drives Article 5(1). |
| `expected_forward_12m` | Contracted or forecast spend. **This is the field most implementations get wrong.** It is not derived; it must be fed from the PO or contract value, because both Article 3(4) and Article 6(2) have forward-looking limbs. |
| `risk_events` | Address and key-employee changes, for the Article 3(3)(a) counters. |
| `risk_justifications` | `{clause_id: text}`. Presence of a justification clears a triggered indicator, per Article 3(3)(b). |

### `Supply`

Carries the facts that switch conditional clauses on: `payment_method`, `third_party_in_payment`, `payee_country`, `supplier_is_intermediary`, `is_goods`. Each maps to an `applies_when` predicate in the registry.

### `Evidence`

```python
Evidence(kind, reference, obtained_on, sha256=None, expires_on=None, note="")
```

`kind` is matched against `Clause.evidence_kinds`. `is_valid_on(as_of)` returns `False` for a document obtained after the assessment date or expired before it, which is how an expired representative ID fails clause 3.1.b.2 rather than silently passing.

`sha256` is unused by the engine and reserved for content addressing when you wire this to a document store. Populate it. It is what turns "we had a certificate" into "we had *this* certificate", which is the difference under audit.

### `HumanConclusion`

The record that satisfies a `JUDGMENT` clause. `decided_by`, `decided_on` and `rationale` are mandatory in practice; `ai_draft_id` is the audit trail back to any model output that informed the decision.

---

## 4. Threshold API

All money is `Decimal`. Never pass `float`. The boundaries are exact and floats will eventually put you on the wrong side of one.

```python
from fta13 import thresholds as T

a = T.assess(
    as_of=date(2026, 11, 15),
    prior_supplies=[...],              # this supplier only
    expected_forward_12m=Decimal("600000"),
    consideration_ex_vat=Decimal("2400"),   # None for supplier-level assessment
)
a.verification_required        # net Article 6 position for this supply
a.enhanced_checks_required     # Article 3(4)
a.basis()                      # list[str], the explanation for the register
```

### Constants

| Constant | Value | Clause |
|---|---|---|
| `DE_MINIMIS_PER_SUPPLY` | 10,000 | 6(1) |
| `DE_MINIMIS_WITHDRAWAL` | 100,000 | 6(2) |
| `ENHANCED_CHECKS_TRIGGER` | 375,000 | 3(4) |
| `RISK_EVENT_LIMIT` | 2 | 3(3)(a)(1)–(2) |
| Window rule | Previous 12 calendar months | throughout |

### Comparison semantics

The Decision uses "less than" for the de minimis and "exceeds" for both ceilings. Encoded as:

```python
de_minimis_available = consideration < 10_000          # strict
withdrawn            = trailing > 100_000 or forward > 100_000   # strict
enhanced             = trailing > 375_000 or forward > 375_000   # strict
```

Exactly 100,000.00 does **not** withdraw the de minimis. Exactly 375,000.00 does **not** trigger enhanced checks. This is tested at all three boundaries in both directions; if you change it, the tests will tell you.

### Other functions

| Function | Purpose |
|---|---|
| `trailing_total(supplies, as_of, include_as_of_date=True)` | Rolling 12-month spend |
| `crossing_date(supplies, threshold)` | First date the rolling total exceeded a threshold |
| `retrospective_exposure(supplies)` | Supplies taken under the de minimis before crossing AED 100,000 |
| `needs_supplier_verification(verified_on, as_of)` | Article 5(1) |
| `reverification_due(verified_on)` | Same calendar date in the following year |
| `count_risk_events(events, kind, as_of)` | Article 3(3)(a) counters |

---

## 5. Clause registry

`clauses.py` is data. Adding a requirement means adding a row, not editing engine logic.

```python
Clause(
    id="4.3.e",
    article="4(3)(e)",
    requirement="Plain-language statement of what must be true.",
    kind=CheckKind.DOCUMENT,          # DOCUMENT | COMPUTED | JUDGMENT
    level="supply",                    # "supplier" | "supply"
    evidence_kinds=("customs_entry",), # DOCUMENT only
    applies_when=lambda ctx: ctx.supply.is_goods,
    blocking=True,
    note="",
)
```

### Choosing `kind`

This is the only decision that matters, and the test is simple.

- **`DOCUMENT`** — the question is "does a valid document of type X exist on the file?" Deterministic. The engine answers it.
- **`COMPUTED`** — the question is answerable by arithmetic over structured data. Deterministic. Requires a branch in `_evaluate_computed`, so it is the one kind that is not pure data.
- **`JUDGMENT`** — the question needs a person to conclude. The engine will not satisfy it without a `HumanConclusion`.

If you are tempted to make a `JUDGMENT` clause `COMPUTED` because you have a clever heuristic, do not. A heuristic that produces a verdict is a model with worse documentation. Put the heuristic in the AI layer as a signal, and leave the conclusion with a human.

### `applies_when` contract

Receives a `Context(supplier, supply, as_of, enhanced_checks_required)`. Must be pure, must be total (no exceptions on partial data), and must be cheap. It is evaluated on every clause on every call.

For supplier-level evaluation `ctx.supply` is `None`, so a supplier clause must never touch it.

### `blocking`

`blocking=False` is reserved for advisory signals. Article 3(3) checks are blocking because a triggered indicator creates a mandatory documentation obligation; the indicator can be resolved by retaining the required clear, justified explanation.

---

## 6. Engine API

```python
out = evaluate_supplier(supplier, as_of=date.today(), prior_supplies=[...])
out = evaluate_supply(supplier, supply, prior_supplies=[...])
```

`prior_supplies` must contain only that supplier's supplies. The engine does not filter by `supplier_id`; passing a mixed list will inflate the trailing total. This is deliberate rather than defensive, because the caller owns the query and silently filtering would hide a bad one.

### `VerificationOutcome`

| Member | Meaning |
|---|---|
| `results` | One `ClauseResult` per registry clause, including `NOT_APPLICABLE` |
| `blocking_gaps` | Blocking clauses that are `MISSING` or `FAILED` |
| `open_judgments` | `JUDGMENT` clauses awaiting sign-off — the reviewer's work queue |
| `decision_13_verification_complete` | Applicable Decision 13 checks have no blocking gaps. This is not an overall input-tax recoverability conclusion. |
| `warnings` | Non-verdict signals: stale verification, out-of-scope, retrospection exposure |
| `register_row()` | Flat dict for the Article 5(3) register |

`evaluate_supply` returns early with an empty `results` list when Article 6(1) makes the exception available. Check `assessment.verification_required` before reading `results`.

---

## 7. AI layer

### Contract

```python
from fta13 import ai

draft = ai.draft("4.3.b", facts={
    "invoice_lines": [...],
    "licensed_activities": supplier.licensed_activities,
    "supplier_trading_history": {...},
})

draft.provisional_view      # True | False | None
draft.confidence            # "high" | "medium" | "low"
draft.rationale             # the text a reviewer edits
draft.missing_information   # what the model could not resolve
draft.contradictions        # facts that conflict with each other
```

`draft()` raises `ValueError` if the clause is `COMPUTED` or `DOCUMENT`, and raises again if the clause is a `JUDGMENT` clause outside the `AI_ASSISTABLE` set. Both are guardrails, not accidents: widening `AI_ASSISTABLE` is a deliberate act with a reviewer's name attached.

### Why `contradictions` is a first-class field

Several clauses require that an explanation **not contradict** other evidence or information available to the taxable person. Articles 3(3)(b) and 4(2)(a) both say so explicitly. That is exactly the task a model is good at and a human reviewer is bad at, because it means holding twenty documents in mind at once. Surfacing contradictions is arguably the highest-value thing the AI layer does, well above producing a view.

### Swapping the client

`draft(clause_id, facts, client=...)` accepts anything exposing `.messages.create(...)` with the Anthropic shape. Pass a fake in tests; pass a Bedrock or Vertex client in a regulated deployment where data residency matters. `_default_client()` is only the convenience path.

### Prompt versioning

`SYSTEM` and `MODEL` are module constants. If you change either, treat it as a breaking change to the audit trail: past drafts were produced under different conditions. Pin them in your deployment and record the version alongside `draft_id`. The current `draft_id` format is `{clause_id}:{facts_digest}:{timestamp}`; extending it with a prompt hash is a sensible early change.

---

## 8. Invariants

Things that must remain true. Each is worth a test if you extend the system.

1. `engine.py` and `thresholds.py` never import `ai`.
2. Two `evaluate_*` calls on identical inputs return identical outputs.
3. No code path sets `ClauseResult.verdict` from an `AIDraft`.
4. `AIDraft.accept()` cannot be called without a non-empty `decided_by`.
5. Every `JUDGMENT` clause is `MISSING` until a matching `HumanConclusion` exists.
6. Money is `Decimal` throughout. No `float` in any arithmetic path.
7. Threshold comparisons are strict (`<`, `>`), never `<=` or `>=`.

---

## 9. Integration notes

### Where the data comes from

| Input | Likely source |
|---|---|
| `Supply` records | AP subledger or ERP invoice lines, VAT-exclusive |
| `expected_forward_12m` | PO value, framework agreement, or FP&A forecast. Manual entry is acceptable and often better. |
| `Evidence` | Document management system, one record per retained file |
| `risk_events` | Supplier master change log. If you do not have one, start one now; the Article 3(3) counters are unanswerable without it. |
| `HumanConclusion` | Your review workflow, whatever it is |

The `risk_events` requirement is the one that usually forces a change upstream. Most supplier masters overwrite the address field rather than versioning it, which makes "changed more than twice in twelve months" impossible to answer after the fact. Turn on change data capture on the supplier master before 1 October 2026, or you will be reconstructing it from correspondence.

### Suggested jobs

| Job | Cadence | Calls |
|---|---|---|
| Supplier monitoring | Monthly | `evaluate_supplier` for every active supplier; alert on suppliers newly crossing 100,000 or 375,000 |
| Re-verification sweep | Monthly | `needs_supplier_verification` across the master |
| Invoice gate | Per invoice | `evaluate_supply` before payment approval |
| Pre-return control | Per VAT period | Assert `decision_13_verification_complete`, alongside the separate VAT-law recovery tests |

The pre-return control is the one that matters. It is the machine equivalent of the Supervisor's sign-off in the policy document, and it is what turns this from a checklist into a control.

### Persistence

Deliberately not included. `register_row()` returns a flat dict that maps cleanly to a table; the outcome objects are dataclasses and serialise with `dataclasses.asdict`. Store outcomes immutably and append rather than update, because the question under audit is "what did you conclude at the time", not "what do you conclude now".

---

## 10. Testing

```bash
python -m pytest tests/ -q
coverage run -m pytest -q && coverage report --include="fta13/*"
```

28 tests and 92% deterministic coverage at release review. CI runs on 3.10 through 3.12 and gates the deterministic layer at 90%, excluding `ai.py` because it is network-bound and mocked rather than exercised.

Boundary tests are the ones to preserve through any refactor:

| Test | Guards |
|---|---|
| `test_de_minimis_is_strictly_below_10k` | 9,999.99 / 10,000 / 10,000.01 |
| `test_100k_ceiling_is_strictly_exceeds` | exactly 100,000 does not withdraw |
| `test_enhanced_checks_not_applicable_below_trigger` | exactly 375,000 does not trigger |
| `test_exactly_two_changes_does_not_trigger` | "more than twice" is 3 |
| `test_small_supply_in_scope_once_supplier_crosses_100k` | the Article 6(2) interaction |
| `test_forward_expectation_alone_withdraws_de_minimis` | forward limb with nil history |
| `test_window_rolls_and_drops_old_supplies` | rolling calendar-month boundary |
| `test_ai_layer_refuses_computed_and_document_clauses` | the layer boundary |
| `test_accepting_a_draft_requires_a_named_human` | the sign-off gate |

---

## 11. Encoded ambiguities

The Decision is silent on several points. Each is resolved explicitly rather than assumed, and each is a place to take advice before relying on the default.

| Question | Default | Where |
|---|---|---|
| Does crossing AED 100,000 reach back to supplies already taken under the de minimis? | Not answered. Population surfaced instead. | `retrospective_exposure()` |
| Do supplies dated on the assessment date count toward the trailing total? | Yes | `trailing_total(include_as_of_date=True)` |
| Is the 12-month window 365 days or 12 calendar months? | 12 calendar months | `window_start()` |
| Does "verified over the previous 12 months" mean the verification date must fall inside the window? | Yes, inclusive of the boundary | `needs_supplier_verification` |
| What happens when a risk indicator applies? | The indicator is not an automatic disqualification, but verification is incomplete until the Article 3(3)(b) explanation is retained. | Article 3(3) clause results |

Where the answer is genuinely unclear, the code surfaces the question rather than picking quietly. That is the behaviour you want in a compliance tool: a silent default is a position you did not know you had taken.

---

## 12. Forking for another jurisdiction

The engine is regime-agnostic. A second jurisdiction needs a new clause registry and, usually, new constants. Everything in `engine.py` stays.

What typically needs to change:

1. **Constants and comparison semantics.** Some regimes use "at or above" rather than "exceeds".
2. **Window definition.** Calendar-year or tax-period windows need a different `window_start`.
3. **Registry.** New `Clause` rows, new `applies_when` predicates.
4. **`CheckKind` allocation.** Regimes that rely on system data rather than buyer-side files (Tanzania's EFD and TRA return matching, for instance) push work from `JUDGMENT` into `COMPUTED`, which makes them cheaper to automate and harder to fake.

A registry-per-regime layout with an effective-date field would let one deployment serve amendments over time. Worth doing before the first amendment to this Decision lands, not after.

---

*Built from the unofficial English translation of Decision No. 13 of 2026. Confirm against the Arabic text in the Official Gazette. Not tax advice.*
