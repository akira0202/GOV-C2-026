"""AgentCore Platform v1.0 — ComplianceClauseInjectNode (inner) for GOV-C2-026."""

# Deterministic (no LLM). Inject the mandatory clauses required for the active law_jurisdiction into
# the spec draft, GROUNDED on the bundled compliance_clause_library (committed authoritative text).
# A mandatory clause with no library text FAILS CLOSED (never an ungrounded placeholder draft). The
# library version is projected as kb_version for the S-4 audit artifact.
# FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only;
# one S-4 event; fail-closed; no S-2.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import clause_injection, clause_library
from src.services.domain_errors import DomainComputeError
from src.services.failure_message import explain
from src.services.progress import emit_progress


class ComplianceClauseInjectNode(FunctionNode):
    """Inner deterministic node — inject mandatory jurisdiction clauses (grounded; fail-closed)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        # Resolve the clause library at construction: a config override (resolved dict) wins, else the
        # bundled base is loaded. A malformed / missing library fails closed here (ConfigError-like
        # fault surfaced at construction), so the graph never wires an ungrounded clause source.
        lib = clause_library.resolve_library(cfg.get("compliance_clause_library"))
        self._clause_library: dict[str, Any] = lib["clauses"]
        self._kb_version: str = lib["version"]

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        spec_draft = state.get("spec_draft")
        if not isinstance(spec_draft, str) or not spec_draft.strip():
            emit_trace_event("clause_inject_failed", {"correlation_id": corr, "reason": "missing_spec_draft"}, state)
            emit_progress(explain("missing_spec_draft"))
            return {"status": AgentStatus.ERROR.value, "error_log": ["ComplianceClauseInjectNode: spec_draft missing"]}
        vi_raw = state.get("validated_input")
        vi = vi_raw if isinstance(vi_raw, dict) else {}
        law_jurisdiction = vi.get("law_jurisdiction", "")
        procurement_type = vi.get("procurement_type", "")
        try:
            result = clause_injection.inject(
                spec_draft,
                law_jurisdiction=law_jurisdiction,
                procurement_type=procurement_type,
                clause_library=self._clause_library,
            )
        except DomainComputeError as exc:
            # a mandatory clause is ungrounded — fail closed, never emit an ungrounded draft
            emit_trace_event("clause_inject_failed", {"correlation_id": corr, "detail": str(exc)}, state)
            emit_progress(explain("clause_inject_failed"))
            return {"status": AgentStatus.ERROR.value, "error_log": [f"ComplianceClauseInjectNode: {exc}"]}
        emit_trace_event(
            "clauses_injected",
            {"correlation_id": corr, "clauses": len(result["clause_list"]), "kb_version": self._kb_version},
            state,
        )
        return {
            "spec_with_clauses": result["spec_with_clauses"],
            "clause_list": result["clause_list"],
            "kb_version": self._kb_version,
            "status": AgentStatus.SUCCESS.value,
        }
