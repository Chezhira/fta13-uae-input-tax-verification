# fta13

Verification workflow engine for **FTA Decision No. 13 of 2026** (UAE), effective 1 October 2026.

> **Important:** This project assesses completion of the verification measures in
> Decision No. 13 only. It does not determine overall input-tax recoverability
> under UAE VAT law. It uses the unofficial English translation and is not tax advice.

```bash
python -m pytest tests/ -q     # 28 tests
python demo.py                 # worked example
streamlit run app.py           # public scenario tester
```

## The architecture question

The temptation with a rule like this is to hand the whole thing to a model and ask "can we claim this input tax?" That fails for a specific reason. Article 5(3) requires the taxpayer to enable the Authority to verify the correctness of implementation. A model output is not reproducible, cannot be recomputed by an officer two years later, and has no accountable signatory. It cannot be the thing a deduction rests on.

So the codebase draws a hard line, and enforces it in code rather than in convention.

### Layer 1 — deterministic (`thresholds.py`, `engine.py`)

Pure functions over structured data. Same inputs, same output, always. This layer owns:

| What | Clause |
|---|---|
| AED 10,000 per-supply de minimis | 6(1) |
| AED 100,000 supplier ceiling that withdraws the de minimis | 6(2) |
| AED 375,000 enhanced checks trigger | 3(4) |
| Rolling 12-month windows, trailing and forward | throughout |
| Risk event counting (address, key employee) | 3(3)(a)(1)–(2) |
| Re-verification due dates | 5(1) |
| Whether required evidence exists and is valid at the date | 3(1), 3(2)(a), 3(4)(a), 4(2)(b), 4(3)(c) |
| Which clauses apply to this supplier and this supply | 3, 4 |
| Whether applicable Decision 13 verification is complete | net position |

The Decision 13 completion status is arithmetic over the clause results. No model is in that path.

### Layer 2 — advisory AI (`ai.py`)

Reserved for the clauses that genuinely need reading and reasoning: whether premises fit the activity, whether a supply falls inside a licence, whether a margin is off market, whether an intermediary's role makes commercial sense, adverse media screening.

`draft()` returns an `AIDraft`. A draft carries a provisional view, a confidence, a list of missing information, and a list of contradictions in the facts supplied. It changes nothing. It becomes part of the record only through:

```python
conclusion = draft.accept(
    decided_by="F. Controller",
    decided_on=date.today(),
    conclusion=False,          # the human's call, not the model's
)
```

`accept()` requires a named individual and takes the conclusion as an explicit argument, so there is no code path that promotes a model's view into the record on its own. The draft id is retained on the conclusion, which gives you the audit trail: what the model saw, what it said, who overrode it and when.

`draft()` raises if you ask it to opine on a `COMPUTED` or `DOCUMENT` clause. Thresholds are never a model's business.

## Judgment calls encoded, and why

The Decision leaves gaps. Each one is resolved explicitly and flagged rather than buried:

- **"Exceeds" is strictly greater than.** AED 100,000.00 exactly does not withdraw the de minimis; 100,000.01 does. Tested at the boundary.
- **The 12-month window rolls.** It is implemented as twelve calendar months from the assessment date, not as a tax period or a fixed 365-day approximation.
- **The forward-looking limbs bite at onboarding.** A supplier with no history but a AED 600,000 framework agreement is in enhanced checks from day one. This is the trap in Article 3(4) and Article 6(2), and it is the case most spreadsheets miss.
- **A triggered risk indicator creates a documentation obligation.** The indicator itself is not automatically disqualifying, but verification remains incomplete until the required clear, justified explanation is retained.
- **Retrospection is left open.** The Decision does not say whether crossing the AED 100,000 ceiling reaches back to supplies already taken under the de minimis. `retrospective_exposure()` does not answer that. It returns the population at issue so a human can take a documented position. That is the honest behaviour for an ambiguity.
- **Trailing totals include the assessment date by default.** Silent in the text, conservative either way, and configurable.

## Extending it

The clause registry in `clauses.py` is data, not logic. Adding a requirement means adding a `Clause` with a `kind` and an `applies_when` predicate; the engine picks it up. That is also how you would fork this for another jurisdiction: same engine, different registry.

## Not included

Persistence, the document management integration, and the actual evidence collection. `Evidence.sha256` is there for content addressing when you wire it to a real store. The register row from `VerificationOutcome.register_row()` is designed to land straight in a table.

---

Built from the unofficial English translation. Confirm against the Arabic text in the Official Gazette.
