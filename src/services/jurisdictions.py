"""AgentCore Platform v1.0 — canonical procurement-law jurisdictions for GOV-C2-026.

Single source of truth for the set of law regimes this template applies jurisdiction-specific
rules and mandatory clauses to (会計法 central / 地方自治法 municipal). The deterministic rule
engine and the clause-injection service both key recognition off this set so an unrecognized
jurisdiction is surfaced (never silently swallowed) consistently. Matching is substring-based —
a caller value such as ``地方自治法（自治体）`` still resolves to its regime.
"""

from __future__ import annotations

KNOWN_JURISDICTIONS = ("会計法", "地方自治法")


def is_recognized(law_jurisdiction: object) -> bool:
    """True when the jurisdiction string contains a known regime. Empty/unset returns False;
    callers treat empty as the zero-config case (base rules only) and only flag a *non-empty*
    value that matches nothing as unrecognized."""
    s = str(law_jurisdiction or "")
    return any(k in s for k in KNOWN_JURISDICTIONS)
