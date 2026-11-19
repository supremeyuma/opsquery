"""Explicit refusal policy.

A refusal is a feature: the agent stops before a tool call and says why. The
categories are stable so the eval and trace can count them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from opsquery.agent.planner import refuse_reason

PII_COLUMN_REQUEST = re.compile(
    r"\b(show|list|give|return|display|export)\b.{0,50}"
    r"\b(customer|user)?\s*(emails?|phone numbers?|phone|contact details)\b",
    re.I,
)
LOW_CONFIDENCE = 0.15


@dataclass(frozen=True)
class RefusalDecision:
    refuse: bool
    reason: str | None = None
    category: str | None = None

    @classmethod
    def allow(cls) -> RefusalDecision:
        return cls(refuse=False)

    @classmethod
    def refuse_with(cls, category: str, reason: str) -> RefusalDecision:
        return cls(refuse=True, reason=reason, category=category)


def _categorise(reason: str) -> str:
    lower = reason.lower()
    if "injection" in lower:
        return "injection"
    if "destructive" in lower:
        return "destructive"
    if "personal data" in lower:
        return "pii"
    if "outside" in lower:
        return "out_of_scope"
    return "unsupported"


def check_request(question: str) -> RefusalDecision:
    """Refuse destructive, out-of-scope, injected and raw-PII requests."""

    reason = refuse_reason(question)
    if reason is not None:
        return RefusalDecision.refuse_with(_categorise(reason), reason)
    if PII_COLUMN_REQUEST.search(question):
        return RefusalDecision.refuse_with(
            "pii", "raw personal data is outside the aggregate allowlist"
        )
    return RefusalDecision.allow()


def check_retrieval_confidence(scores: list[float]) -> RefusalDecision:
    """Refuse a document answer when retrieval found nothing relevant."""

    if not scores or max(scores) < LOW_CONFIDENCE:
        return RefusalDecision.refuse_with(
            "low_confidence", "no sufficiently relevant document was retrieved"
        )
    return RefusalDecision.allow()
