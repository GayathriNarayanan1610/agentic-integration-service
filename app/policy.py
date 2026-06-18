"""Pure business policy — no I/O, so it is trivially unit-testable.

Keeping the "is this a risky action?" decision as a pure function (rather than
burying it in the agent) means the guardrail is deterministic and verifiable,
independent of whatever the LLM decides.
"""
from __future__ import annotations


def requires_approval(estimated_value: float, threshold: float) -> bool:
    """A ticket on a high-value account must be approved by a human first."""
    return estimated_value >= threshold


def suggest_priority(estimated_value: float, threshold: float) -> str:
    """Deterministic priority fallback the agent can lean on."""
    if estimated_value >= threshold:
        return "urgent"
    if estimated_value >= threshold / 4:
        return "high"
    return "normal"
