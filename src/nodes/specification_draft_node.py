"""AgentCore Platform v1.0 — SpecificationDraftNode (inner) for GOV-C2-026."""

# LLM node: generate the 総務省 / 内閣府-format 仕様書 DRAFT from the normalized requirements and the
# deterministic compliance_flags, grounded on the injected procurement_law_kb reference. Drafts the
# prose ONLY — it does not inject mandatory clauses (compliance_clause_inject owns that), generate the
# reviewer checklist, judge legal compliance, or approve/submit. FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary);
# delta-only; one S-4 event; no S-2. error_strategy = propagate: an unavailable / failing / malformed
# LLM is a hard error, never a partial or LLM-less pseudo-legal draft.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import specification_drafting
from src.services.failure_message import GENERATION_UNAVAILABLE
from src.services.progress import emit_progress
from src.services.failure_message import explain


class SpecificationDraftNode(FunctionNode):
    """Inner LLM node — draft the specification prose (fail-closed)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm
        cfg = node_config or {}
        # KB grounding reference (procurement_law_kb), injected by the graph; optional context.
        self._kb = cfg.get("procurement_law_kb")

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        normalized = state.get("normalized_requirements")
        if not isinstance(normalized, dict):
            emit_trace_event("specification_draft_failed", {"correlation_id": corr, "reason": "missing_input"}, state)
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SpecificationDraftNode: normalized_requirements missing"],
            }
        if self._llm is None:
            emit_progress(GENERATION_UNAVAILABLE)
            emit_trace_event("specification_draft_failed", {"correlation_id": corr, "reason": "llm_unavailable"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SpecificationDraftNode: LLM unavailable (error_strategy=propagate)"],
            }
        try:
            messages = specification_drafting.build_spec_messages(normalized, state.get("compliance_flags"), self._kb)
            emit_progress("Drafting the specification...")
            text = self._llm.complete(messages, state)
            spec = specification_drafting.parse_and_validate_spec(text)
        except Exception as exc:  # LLM failure / timeout / malformed draft → hard error (propagate)
            emit_progress(GENERATION_UNAVAILABLE)
            emit_trace_event("specification_draft_failed", {"correlation_id": corr, "error": type(exc).__name__}, state)
            return {"status": AgentStatus.ERROR.value, "error_log": [f"SpecificationDraftNode: {type(exc).__name__}"]}
        emit_trace_event("spec_drafted", {"correlation_id": corr, "length": len(spec)}, state)
        return {"spec_draft": spec, "status": AgentStatus.SUCCESS.value}
