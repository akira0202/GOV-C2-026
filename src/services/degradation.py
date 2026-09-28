"""AgentCore Platform v1.0 — which rejections are answered, and which stay fail-closed.

A run that ends without a specification draft is reported one of two ways, and the split is by
**whose problem it is**. A request the caller can correct completes carrying an allow-listed
``diagnostic_code``; the caller-facing sentence is the one ``failure_message.explain()`` already
derives from that code, so this module keeps no second catalogue of wording. A refusal or a fault
this agent owns stays ``status=ERROR`` with no output.

**The line this template draws.** The screen that decides whether the body is a document at all —
non-text input, a NUL byte, a flood of control characters — is a refusal and stays one: it exists
so such a payload is never parsed. Everything decided *about a parsed request* is the caller's to
correct: the ceiling on how much can be sent at once, a body that is not a JSON object, the
required procurement objective and budget, the per-field length and character contracts, the
procurement type and law jurisdiction (whether supplied per call or left to the deployment
default), and the typed contract on the optional fields. Each names something the caller can change
and send again — including a control character inside one field, which is a contract on a value in
a body this agent has already accepted as text.

``procurement_type_unresolved`` is answered for the same reason: whatever the deployment default
is, the caller can name the type in the request and send it again.

**Fail-closed (unchanged).**

- ``s2_non_text`` / ``s2_non_text_type`` — the document screen above.
- ``output_gated`` — the terminal S-3 gate. The advisory posture is not negotiable, and a draft
  that reaches it with that posture broken must not leave.
- Everything inside the workflow: the handoffs (``empty_handoff``, ``malformed``, ``shape``, the
  ``missing_*`` inputs between nodes), an unusable drafting model, a compliance-rule fault, and a
  mandatory clause whose source text is absent from the clause library — an ungrounded draft is
  never emitted. These are this deployment's configuration or this agent's own invariants.

Only ``pre_process`` degrades, so no marker has to cross the inner graph boundary in this template
— ``main`` simply skips the workflow when the gate has already answered.
"""

from __future__ import annotations

from src.services.failure_message import explain

# Codes the outer input gate answers rather than refuses.
CALLER_CORRECTABLE_CODES = frozenset(
    {
        "s2_oversized",
        "empty_or_missing",
        "malformed",
        "too_deep",
        "not_object",
        "missing_objective",
        "objective_too_long",
        "objective_invalid",
        "missing_budget",
        "procurement_type_type",
        "procurement_type_unresolved",
        "procurement_type_invalid",
        "law_jurisdiction_type",
        "law_jurisdiction_invalid",
        "optional_contract",
    }
)

_FALLBACK = "The request could not be completed and no specification draft was produced."


def is_caller_correctable(code: str) -> bool:
    """True when this rejection names something the caller can change and send again."""
    return code in CALLER_CORRECTABLE_CODES


def explain_degradation(code: str) -> str:
    """Return the caller-facing sentence for a degradation code.

    Delegates to ``failure_message.explain()``, which derives the sentence from the allow-listed
    code alone — only the field name the code itself carries is ever substituted, so no request
    value, procurement objective or budget figure can appear.
    """
    return explain(code) if code else _FALLBACK
