"""AgentCore Platform v1.0 — RequirementsNormalizeNode (inner) for GOV-C2-026."""

# LLM node: parse the validated NL procurement input into the fixed requirement schema
# (purpose / scope / technical_requirements / evaluation_criteria / timeline / budget_ceiling).
# FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; no S-2 (outer input_validate gated).
# error_strategy = propagate: an unavailable / failing / malformed LLM is a hard error (status:
# error), NEVER a partial or LLM-less pseudo-normalization. The LLM key is resolved by the adapter
# via ctx.secrets; the trace carries only counts / error type, never the raw procurement body.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import requirements_normalization
from src.services.failure_message import GENERATION_UNAVAILABLE
from src.services.progress import emit_progress
from src.services.failure_message import explain


class RequirementsNormalizeNode(FunctionNode):
    """Inner LLM node — normalize NL requirements into the fixed schema (fail-closed)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        validated = state.get("validated_input")
        if not isinstance(validated, dict):
            emit_trace_event(
                "requirements_normalize_failed", {"correlation_id": corr, "reason": "missing_input"}, state
            )
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["RequirementsNormalizeNode: validated_input missing"],
            }
        if self._llm is None:
            emit_progress(GENERATION_UNAVAILABLE)
            emit_trace_event(
                "requirements_normalize_failed", {"correlation_id": corr, "reason": "llm_unavailable"}, state
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["RequirementsNormalizeNode: LLM unavailable (error_strategy=propagate)"],
            }
        try:
            messages = requirements_normalization.build_normalize_messages(validated)
            emit_progress("Normalising the requirements...")
            text = self._llm.complete(messages, state)
            normalized = requirements_normalization.parse_and_validate_requirements(text)
        except Exception as exc:  # LLM failure / timeout / malformed schema → hard error (propagate)
            emit_progress(GENERATION_UNAVAILABLE)
            emit_trace_event(
                "requirements_normalize_failed", {"correlation_id": corr, "error": type(exc).__name__}, state
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"RequirementsNormalizeNode: {type(exc).__name__}"],
            }
        emit_trace_event("requirements_normalized", {"correlation_id": corr, "fields": len(normalized)}, state)
        return {"normalized_requirements": normalized, "status": AgentStatus.SUCCESS.value}
