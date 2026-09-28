"""AgentCore Platform v1.0 — S-3 output-gate helpers for GOV-C2-026.

Pure functions (no framework imports; unit-testable standalone). The advisory disclaimer is
node-owned (never dependent on the LLM output remaining intact) and is injected into EVERY
output — a Markdown string draft or a structured dict — so the output is always framed as a
draft for human review, never a final legal instrument.
"""

from __future__ import annotations

from typing import Any

ADVISORY_DISCLAIMER = (
    "Draft for human review only. Not a final legal instrument, regulatory " "submission, or procurement approval."
)


def ensure_disclaimer(result: Any) -> Any:
    """Return ``result`` with the advisory disclaimer guaranteed present. A string draft gets the
    disclaimer prepended (idempotent); a dict gets a ``disclaimer`` field; any other value is
    wrapped so the disclaimer cannot be lost."""
    if isinstance(result, str):
        if ADVISORY_DISCLAIMER in result:
            return result
        return f"{ADVISORY_DISCLAIMER}\n\n{result}"
    if isinstance(result, dict):
        out = dict(result)
        out["disclaimer"] = ADVISORY_DISCLAIMER
        return out
    return {"output": result, "disclaimer": ADVISORY_DISCLAIMER}


def has_disclaimer(result: Any) -> bool:
    """True when the advisory disclaimer is present in the (string or dict) output."""
    if isinstance(result, str):
        return ADVISORY_DISCLAIMER in result
    if isinstance(result, dict):
        return result.get("disclaimer") == ADVISORY_DISCLAIMER
    return False
