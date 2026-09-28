"""AgentCore Platform v1.0 — AuditMetadataEmitNode (inner, terminal) for GOV-C2-026."""

# Deterministic S-4 audit node (terminal inner node). Assembles the audit_metadata artifact
# (data_hash / generated_at / kb_version / model_version / law_jurisdiction) — hashes only, no
# raw procurement body. FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; fail-closed;
# no S-2. The inner get_output() projects {output, audit_metadata} to the outer graph from the
# fields this node and its upstream produce.

from datetime import datetime, timezone
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import audit_metadata
from src.services.failure_message import explain
from src.services.progress import emit_progress

_JST = timezone.utc  # audit timestamp in UTC; the report itself uses JST where user-facing


class AuditMetadataEmitNode(FunctionNode):
    """Inner terminal node — assemble the S-4 audit_metadata artifact (hashes only)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        self._kb_version = cfg.get("kb_version", "bundled")
        self._model_version = cfg.get("model_version", "unspecified")

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        spec_with_clauses = state.get("spec_with_clauses")
        if not isinstance(spec_with_clauses, str):
            emit_trace_event("audit_emit_failed", {"correlation_id": corr, "reason": "missing_input"}, state)
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["AuditMetadataEmitNode: spec_with_clauses missing"],
            }
        law_jurisdiction = (
            (state.get("validated_input") or {}).get("law_jurisdiction")
            if isinstance(state.get("validated_input"), dict)
            else None
        )
        # kb_version is bound to the clause-library revision actually used (projected by
        # compliance_clause_inject), so the audit traces the output to its legal-content source —
        # never an unbound config default. Fall back to config only if absent.
        kb_version = state.get("kb_version") or self._kb_version
        meta = audit_metadata.build_audit_metadata(
            spec_with_clauses=spec_with_clauses,
            clause_list=state.get("clause_list"),
            reviewer_checklist=state.get("reviewer_checklist"),
            law_jurisdiction=law_jurisdiction,
            kb_version=kb_version,
            model_version=self._model_version,
            generated_at=datetime.now(_JST).isoformat(),
        )
        emit_trace_event("audit_emitted", {"correlation_id": corr, "data_hash": meta["data_hash"][:12]}, state)
        return {"audit_metadata": meta, "status": AgentStatus.SUCCESS.value}
