"""AgentCore Platform v1.0 — ReviewChecklistGenerateNode (inner) for GOV-C2-026."""

# Deterministic (no LLM). Generate the reviewer checklist from the compliance flags + the
# standard legal-review items; mark EVERY item as human-validation-required (国家賠償法).
# FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; fail-closed; no S-2.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import review_checklist
from src.services.failure_message import explain
from src.services.progress import emit_progress


class ReviewChecklistGenerateNode(FunctionNode):
    """Inner deterministic node — reviewer checklist (all items human-validation-required)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        spec_with_clauses = state.get("spec_with_clauses")
        compliance_flags = state.get("compliance_flags")
        if not isinstance(spec_with_clauses, str) or not isinstance(compliance_flags, list):
            emit_trace_event("review_checklist_failed", {"correlation_id": corr, "reason": "missing_input"}, state)
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ReviewChecklistGenerateNode: spec_with_clauses / compliance_flags missing"],
            }
        checklist = review_checklist.generate(spec_with_clauses, compliance_flags)
        emit_trace_event("review_checklist_generated", {"correlation_id": corr, "items": len(checklist)}, state)
        return {"reviewer_checklist": checklist, "status": AgentStatus.SUCCESS.value}
