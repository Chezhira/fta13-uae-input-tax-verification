# Copyright (c) 2026 Chez Solutions. Authored by Zahidah Murira.
# MIT License: https://github.com/Chezhira/fta13-uae-input-tax-verification

"""Advisory AI layer.

Hard rule enforced by design, not by convention: nothing in this module can
change a verdict. `draft()` returns an `AIDraft`. A draft becomes part of the
record only when a named human turns it into a `HumanConclusion` via
`accept()`, which records who decided and pins the draft that informed them.

Rationale: Article 5(3) requires the Authority to be able to verify correct
implementation. A model output is not reproducible and carries no accountable
signatory, so it cannot be the thing the deduction rests on. It can still do the
expensive part, which is reading the documents and writing the first draft.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from .clauses import ALL_CLAUSES
from .models import CheckKind, HumanConclusion

MODEL = "claude-sonnet-4-6"

# Clauses where a model genuinely helps. Everything else stays with the human.
AI_ASSISTABLE = {
    "3.1.b.1.consistency",  # cross-read registry extract vs stated details
    "3.2.b",                # premises vs activity plausibility
    "3.3.a.3",              # disproportionate transactions, given history
    "3.4.b",                # adverse media and review screening
    "3.4.a.validity",       # authorised UAE bank and no reservations
    "4.1.b",                # genuine commercial reasons
    "4.2.a",                # payment terms rationale
    "4.2.a.thirdparty",
    "4.2.a.offshore",
    "4.2.b.cash.compliance",
    "4.3.a",                # price and margin vs market
    "4.3.b",                # supply vs licensed activity
    "4.3.d",                # intermediary role
}

SYSTEM = """You assist a UAE VAT compliance team applying FTA Decision No. 13 of 2026.

You are drafting an ADVISORY assessment of one clause. You are not the decision maker.
A named human reviews and signs off. Write for that reader.

Rules:
- Reason only from the facts supplied. Never assume a fact not given.
- If the facts are insufficient to reach a view, say so and list exactly what is missing.
- Where evidence points both ways, set out both sides. Do not resolve it.
- State a provisional view and a confidence of high, medium or low.
- Flag any point where the supplied facts contradict each other. This matters:
  several clauses require that an explanation not contradict other evidence held.

Return strict JSON, no markdown fences, with keys:
  provisional_view: true | false | null
  confidence: "high" | "medium" | "low"
  rationale: string, 2-4 sentences, the draft a reviewer would edit
  missing_information: array of strings
  contradictions: array of strings
"""


@dataclass(frozen=True)
class AIDraft:
    draft_id: str
    clause_id: str
    model: str
    created_at: datetime
    facts_digest: str
    provisional_view: Optional[bool]
    confidence: str
    rationale: str
    missing_information: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def accept(
        self, *, decided_by: str, decided_on: date, conclusion: bool, rationale: str = ""
    ) -> HumanConclusion:
        """Convert an advisory draft into a signed conclusion.

        `conclusion` is the human's, not the model's. Passing it explicitly is
        deliberate: there is no code path that promotes a draft on its own.
        """
        if not decided_by.strip():
            raise ValueError("a named individual must sign off")
        return HumanConclusion(
            clause_id=self.clause_id,
            conclusion=conclusion,
            rationale=rationale or self.rationale,
            decided_by=decided_by,
            decided_on=decided_on,
            ai_draft_id=self.draft_id,
        )


def _digest(facts: dict) -> str:
    blob = json.dumps(facts, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def draft(clause_id: str, facts: dict, *, client=None) -> AIDraft:
    """Produce an advisory draft for one JUDGMENT clause.

    Raises if asked to opine on a clause the deterministic layer owns. That is
    the point: thresholds and evidence-existence are never a model's business.
    """
    clause = ALL_CLAUSES.get(clause_id)
    if clause is None:
        raise KeyError(f"unknown clause {clause_id}")
    if clause.kind is not CheckKind.JUDGMENT:
        raise ValueError(
            f"{clause_id} is {clause.kind.value} and is decided deterministically. "
            "The AI layer does not opine on it."
        )
    if clause_id not in AI_ASSISTABLE:
        raise ValueError(f"{clause_id} is reserved for unassisted human judgment")

    prompt = (
        f"Clause {clause.article}\n"
        f"Requirement: {clause.requirement}\n\n"
        f"Facts on file:\n{json.dumps(facts, indent=2, default=str)}"
    )

    if client is None:
        client = _default_client()

    resp = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    payload = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())

    fd = _digest(facts)
    return AIDraft(
        draft_id=f"{clause_id}:{fd}:{datetime.now(timezone.utc):%Y%m%dT%H%M%S}",
        clause_id=clause_id,
        model=MODEL,
        created_at=datetime.now(timezone.utc),
        facts_digest=fd,
        provisional_view=payload.get("provisional_view"),
        confidence=payload.get("confidence", "low"),
        rationale=payload.get("rationale", ""),
        missing_information=payload.get("missing_information", []),
        contradictions=payload.get("contradictions", []),
        raw=payload,
    )


def _default_client():
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "pip install anthropic, or pass a client. The deterministic engine "
            "runs fine without it."
        ) from e
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic()
