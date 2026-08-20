# Arabic source reconciliation

This record documents the reconciliation of the implementation to the Arabic text of UAE Federal Tax Authority Decision No. 13 of 2026. The Arabic Decision is authoritative. The FTA's unofficial English translation is used only for English-language labels and descriptions.

## Source identity

- Issuer: UAE Federal Tax Authority
- Decision: No. 13 of 2026
- Issue date: 22 July 2026
- Effective date: 1 October 2026
- Arabic source: [`FTA-Decision-13-2026-Arabic.pdf`](FTA-Decision-13-2026-Arabic.pdf)
- Reconciliation completed: 20 August 2026

## Article mapping

| Arabic Decision | Requirement reconciled | Implementation |
|---|---|---|
| Article 1 | Definitions used by the Decision | Domain terms in `fta13/models.py` |
| Article 2 | Applies to verification before deducting input tax | Scope statements in `README.md` and `app.py` |
| Article 3(1) | Natural-person and legal-person identity checks | Supplier clauses `3.1.a.*` and `3.1.b.*` |
| Article 3(2) | Actual place of business and activity compatibility | Supplier clauses `3.2.a` and `3.2.b` |
| Article 3(3) | Address, key-employee and unusual-transaction risk indicators; documented explanations | Supplier clauses `3.3.a.*` and engine risk-justification controls |
| Article 3(4) | Bank confirmation and public review/media checks when prior or expected 12-month supplies exceed AED 375,000 | `ENHANCED_CHECKS_TRIGGER` and supplier clauses `3.4.*` |
| Article 4(1) | General assessment and genuine commercial reasons | Supply clauses `4.1.*` |
| Article 4(2) | Commercially justifiable payment terms; third-party/offshore explanations; electronic payment or documented cash exception | Supply clauses `4.2.*` |
| Article 4(3) | Price, activity, goods origin/title and intermediary checks | Supply clauses `4.3.*` |
| Article 5(1) | Supplier verification on first dealing or where not verified in the previous 12 months | Reverification logic and blocking clause `5.1` |
| Article 5(2) | Verification of every taxable supply | Supply evaluation workflow |
| Article 5(3) | Documentation and retention of supporting records | Evidence model and downloadable verification record; the public app itself does not retain documents |
| Article 5(4) | Documented policy for responsible persons, review, supervision, powers and responsibilities | Identified as an organisational control outside the public transaction assessment |
| Article 6(1) | Measures may be disregarded where consideration excluding VAT is less than AED 10,000 | `DE_MINIMIS_PER_SUPPLY`; strict `<` comparison |
| Article 6(2) | Exception unavailable where prior or expected 12-month supplier supplies exceed AED 100,000 | `DE_MINIMIS_WITHDRAWAL`; strict `>` comparison |
| Article 7 | Effective from 1 October 2026 | Minimum assessment date and documentation |

## Reconciliation outcome

The implemented thresholds, applicability conditions and clause descriptions align with the Arabic Decision. No calculation-engine change was required during this reconciliation.

Two boundaries remain explicit:

1. Article 5(4) is an entity-level governance obligation and is not represented as a transaction-level completion check in the public app.
2. The Decision does not resolve every operational interpretation, including possible retrospective treatment after the Article 6(2) threshold is crossed. The engine surfaces such matters for documented human review rather than inventing a legal conclusion.

If any English label, project documentation or generated output conflicts with the Arabic Decision, the Arabic text prevails.
