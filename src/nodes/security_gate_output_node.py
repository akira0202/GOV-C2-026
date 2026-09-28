"""AgentCore Platform v1.0 — SecurityGateOutputNode (outer post_process) for GOV-C2-026."""

# Outer post_process node — the S-3 output gate + the final output handoff. execute() (which
# receives the full state) injects the advisory disclaimer into the composed result (node-owned,
# never dependent on the LLM output), BLOCKS the output if advisory_only has been tampered to false,
# and writes the gated draft to `formatted_output` so it reaches the caller
# (AgentBaseGraph.get_output = formatted_output or result). The framework @final
# _security_gate_output() (baseline credential scan) still runs. advisory_only is a CODE-SIDE
# CONSTANT: a config attempting to set it false is rejected at construction (ConfigError).
# FunctionNode; VERIFIED_EXTERNAL; one S-4 event (output_gated).

from typing import Any, ClassVar

from framework.errors import ConfigError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import output_gate
from src.services.progress import emit_progress
from src.services.degradation import explain_degradation
from src.services.failure_message import explain


def _advisory_posture_intact(state: dict[str, Any]) -> bool:
    """Report whether this run still carries the advisory posture the input gate wrote.

    The check has to read the field the run actually holds. ``input_validate`` writes
    ``advisory_only: True`` **inside ``validated_input``** — nothing in this template ever writes a
    top-level ``state["advisory_only"]``, so a gate keyed on that top-level name would inspect a
    field that is always absent and could never fire. Read the carrier instead, and treat a missing
    or reshaped ``validated_input`` as not intact: by the time the draft reaches this gate, the
    canonical validated_input must be there.
    """
    if state.get("advisory_only") is False:
        return False
    validated = state.get("validated_input")
    if not isinstance(validated, dict):
        return False
    return validated.get("advisory_only") is True


class SecurityGateOutputNode(FunctionNode):
    """Outer S-3 output gate — mandatory advisory disclaimer + advisory_only tamper block."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        # advisory_only is a code-side constant (true); a config false is rejected (mirrors the outer
        # input_validate). The advisory posture is never a config toggle.
        if "advisory_only" in cfg and cfg["advisory_only"] is not True:
            raise ConfigError(
                "SecurityGateOutputNode: advisory_only is a code-side constant (true); a config " "false is rejected"
            )

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # execute() receives the full state, so it does the S-3 work here and hands the gated draft
        # to formatted_output. Trace carries no raw procurement body; one S-4 event per invocation.
        emit_progress("Preparing the response...")
        corr = state.get("correlation_id")
        code = state.get("diagnostic_code")
        if code:
            # The input gate answered a request the caller can correct, so there is no draft to
            # gate. Release the notice as the result of the turn. A specification draft with no
            # content is NOT returned in its place: that would be a real artifact for a request
            # this agent never read.
            emit_trace_event("draft_degraded", {"correlation_id": corr, "reason": code}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "diagnostic_code": code,
                "formatted_output": explain_degradation(code),
                "result": None,
            }
        if not _advisory_posture_intact(state):
            # advisory_only is not the code-side true this run must carry — block the output (the
            # advisory posture is non-negotiable)
            emit_trace_event("output_gated", {"correlation_id": corr, "blocked": True}, state)
            emit_progress(explain("output_gated"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SecurityGateOutputNode: advisory_only is not intact — " "output blocked (S-3)"],
            }
        gated = output_gate.ensure_disclaimer(state.get("result"))
        emit_trace_event("output_gated", {"correlation_id": corr, "blocked": False}, state)
        # write the gated draft to the inherited formatted_output so it reaches the caller
        return {"formatted_output": gated, "status": AgentStatus.SUCCESS.value}
