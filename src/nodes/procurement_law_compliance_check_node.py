"""AgentCore Platform v1.0 — ProcurementLawComplianceCheckNode (inner) for GOV-C2-026."""

# Deterministic rule engine (no LLM). Check the normalized requirements against 会計法 /
# 地方自治法 conventions for the active law_jurisdiction; emit rule-based defect flags.
# FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; fail-closed; no S-2.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import compliance_rules
from src.services.domain_errors import DomainComputeError
from src.services.failure_message import explain
from src.services.progress import emit_progress


class ProcurementLawComplianceCheckNode(FunctionNode):
    """Inner deterministic node — rule-based procurement-law compliance flags."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        raw = cfg.get("common_error_rules")
        self._rules: dict[str, Any] = raw if isinstance(raw, dict) else {}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        nr = state.get("normalized_requirements")
        if not isinstance(nr, dict):
            emit_trace_event("compliance_check_failed", {"correlation_id": corr, "reason": "missing_input"}, state)
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ProcurementLawComplianceCheckNode: normalized_requirements missing"],
            }
        law_jurisdiction = (
            (state.get("validated_input") or {}).get("law_jurisdiction", "")
            if isinstance(state.get("validated_input"), dict)
            else ""
        )
        try:
            flags = compliance_rules.check(nr, law_jurisdiction=law_jurisdiction, rules=self._rules)
        except DomainComputeError as exc:
            emit_trace_event("compliance_check_failed", {"correlation_id": corr, "detail": str(exc)}, state)
            emit_progress(explain("compliance_check_failed"))
            return {"status": AgentStatus.ERROR.value, "error_log": [f"ProcurementLawComplianceCheckNode: {exc}"]}
        emit_trace_event(
            "compliance_checked",
            {"correlation_id": corr, "flags": len(flags), "high": sum(1 for f in flags if f["severity"] == "High")},
            state,
        )
        return {"compliance_flags": flags, "status": AgentStatus.SUCCESS.value}
