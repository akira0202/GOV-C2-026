"""AgentCore Platform v1.0 — inner DomainWorkflowGraph + State for GOV-C2-026.

This module owns the inner-graph State schema (`DomainWorkflowState`) and the inner
`BaseGraph` composition: the linear procurement pipeline
``input_hydrate → requirements_normalize → procurement_law_compliance_check →
specification_draft → compliance_clause_inject → review_checklist_generate →
audit_metadata_emit``. The outer `ProcurementWorkflowGraphNode` (assigned to the outer
`main` slot) wraps this graph; `get_output()` / `merge_output()` project ``{output,
audit_metadata}`` to the outer inherited `result`. The LLM adapter and config flow down from
the outer graph config through the GraphNode into the inner graph's node construction.
"""

import json
from typing import Any, ClassVar, NotRequired

from langgraph.graph import END, START

from framework.errors import SubgraphError
from framework.graph.base_graph import BaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

from src.services import stg_error
from src.nodes.audit_metadata_emit_node import AuditMetadataEmitNode
from src.nodes.compliance_clause_inject_node import ComplianceClauseInjectNode
from src.nodes.input_hydrate_node import InputHydrateNode
from src.nodes.procurement_law_compliance_check_node import ProcurementLawComplianceCheckNode
from src.nodes.requirements_normalize_node import RequirementsNormalizeNode
from src.nodes.review_checklist_generate_node import ReviewChecklistGenerateNode
from src.nodes.specification_draft_node import SpecificationDraftNode
from src.services import audit_metadata
from src.services.progress import emit_progress
from src.services.failure_message import explain


class DomainWorkflowState(AgentState):
    """Inner State for the GOV procurement-spec domain workflow (Cat 2 two-layer).

    Flat TypedDict — primitives / JSON-serializable types only (msgpack-safe; no
    Pydantic / dataclass / arbitrary objects). Framework fields are inherited from
    AgentState and are NOT redeclared. Reducer-managed fields are NEVER projected
    inner -> outer.

    `normalized_requirements` / `spec_draft` are produced by the LLM nodes; `audit_metadata`
    by the terminal S-4 audit node; the deterministic nodes produce `compliance_flags` /
    `spec_with_clauses` / `clause_list` / `reviewer_checklist`.
    """

    # requirements_normalize / specification_draft (LLM)
    normalized_requirements: NotRequired[
        dict[str, Any]
    ]  # {purpose, scope, technical_requirements, evaluation_criteria, timeline, budget_ceiling}
    spec_draft: NotRequired[str]  # 総務省 / 内閣府-format 仕様書 draft (Markdown)
    # procurement_law_compliance_check
    compliance_flags: NotRequired[list[Any]]  # rule-based specification-defect flags
    # compliance_clause_inject
    spec_with_clauses: NotRequired[str]  # draft with mandatory clauses injected (grounded)
    clause_list: NotRequired[list[Any]]  # injected clauses {id, source, jurisdiction, missing?}
    kb_version: NotRequired[str]  # clause-library revision used (→ audit kb_version)
    # review_checklist_generate
    reviewer_checklist: NotRequired[list[Any]]  # {item, category, human_validation_required}
    # audit_metadata_emit (terminal S-4 artifact)
    audit_metadata: NotRequired[
        dict[str, Any]
    ]  # {data_hash, generated_at, kb_version, model_version, law_jurisdiction}


class DomainWorkflowGraph(BaseGraph):
    """Inner procurement-spec workflow (Cat 2 inner `BaseGraph`). Linear topology; the LLM
    adapter and per-node config flow in via the graph config (from the outer GraphNode)."""

    @property
    def name(self) -> str:
        return "procurement_specification_workflow"

    @property
    def state_schema(self) -> type:
        return DomainWorkflowState

    def _validate_config(self) -> None:
        # No mandatory inner config: a missing LLM is a fail-closed runtime error at the LLM
        # nodes (error_strategy = propagate), not a construction error.
        pass

    def register_nodes(self) -> None:
        cfg = self.config or {}
        llm = cfg.get("llm")
        self._nodes["input_hydrate"] = InputHydrateNode()
        self._nodes["requirements_normalize"] = RequirementsNormalizeNode(llm=llm)
        self._nodes["procurement_law_compliance_check"] = ProcurementLawComplianceCheckNode(node_config=cfg)
        self._nodes["specification_draft"] = SpecificationDraftNode(node_config=cfg, llm=llm)
        self._nodes["compliance_clause_inject"] = ComplianceClauseInjectNode(node_config=cfg)
        self._nodes["review_checklist_generate"] = ReviewChecklistGenerateNode(node_config=cfg)
        self._nodes["audit_metadata_emit"] = AuditMetadataEmitNode(node_config=cfg)

    # Inner hop order. Each hop is conditional on the running status: a node that returned
    # status:error routes straight to END.
    _CHAIN = (
        "input_hydrate",
        "requirements_normalize",
        "procurement_law_compliance_check",
        "specification_draft",
        "compliance_clause_inject",
        "review_checklist_generate",
        "audit_metadata_emit",
    )

    def add_edges(self) -> None:
        # Every hop is conditional: a node returning status:error routes to END (no partial
        # output), otherwise it advances to the next inner node. An unconditional chain would keep
        # invoking the remaining nodes after a failure — each one is then skipped by the framework
        # input gate, which returns the whole State, and `error_log` (an operator.add channel) is
        # concatenated onto itself once per remaining node, doubling the audit trail at each hop.
        self._sg.add_edge(START, self._CHAIN[0])
        for node, nxt in zip(self._CHAIN, self._CHAIN[1:]):
            self._sg.add_conditional_edges(
                node,
                self._continue_or_end,
                {"continue": nxt, "end": END},
            )
        self._sg.add_edge(self._CHAIN[-1], END)

    def _continue_or_end(self, state: AgentState) -> str:
        """END on any node error; otherwise advance to the next inner node."""
        return "end" if state.get("status") == AgentStatus.ERROR.value else "continue"

    def route(self, state: AgentState) -> str:
        # Required by the BaseGraph ABC; the per-hop edges use _continue_or_end().
        return self._continue_or_end(state)

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the sub_result for the outer GraphNode. Key the success/failure off the presence of
        the assembled specification (not a status enum/string comparison): if the terminal nodes
        produced `spec_with_clauses`, project the human-review output + the audit artifact; otherwise
        carry the error so a failed inner run propagates through the GraphNode."""
        status = state.get("status")
        spec_with_clauses = state.get("spec_with_clauses")
        if isinstance(spec_with_clauses, str) and spec_with_clauses:
            payload = audit_metadata.build_output(
                spec_with_clauses=spec_with_clauses,
                clause_list=state.get("clause_list"),
                reviewer_checklist=state.get("reviewer_checklist"),
            )
            # expose the payload under both keys — the outer GraphNode merge reads one of them
            return {
                "status": status,
                "output": payload,
                "result": payload,
                "audit_metadata": state.get("audit_metadata"),
            }
        return {"status": "error", "error_log": state.get("error_log", [])}


class ProcurementWorkflowGraphNode(GraphNode):
    """Outer `main`-slot GraphNode wrapping the inner DomainWorkflowGraph. error_strategy =
    propagate: an inner failure yields status:error with no partial output (the assembled draft is
    never surfaced)."""

    error_strategy: ClassVar[str] = "propagate"

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._config = node_config or {}
        self._subgraph = DomainWorkflowGraph(config=self._config)

    def get_subgraph(self) -> DomainWorkflowGraph:
        return self._subgraph

    def extract_input(self, state: AgentState) -> str:
        # Hand the validated_input to the inner graph as a JSON string; the inner input_hydrate
        # decodes it (BaseGraph.invoke sets only user_input, not validated_input).
        emit_progress("Drafting the procurement specification...")
        return json.dumps(state.get("validated_input", {}), ensure_ascii=True)

    def execute(self, state: AgentState) -> dict[str, Any]:
        # The input gate answers a request the caller can correct instead of refusing it, and no
        # draft is built for it. Running the workflow here would draft for a request that was never
        # accepted — and its first node would fail on the absent handoff, reporting the missing
        # input as this agent's own fault. Skip it and carry the code to post_process.
        code = state.get("diagnostic_code")
        if code:
            emit_trace_event("workflow_skipped", {"correlation_id": state.get("correlation_id"), "reason": code}, state)
            return {"status": AgentStatus.SUCCESS.value, "diagnostic_code": code}
        # error_strategy=propagate: on an inner failure the framework GraphNode raises SubgraphError
        # (no partial output) — merge_output is NOT called on that path. Convert it to a status:error
        # delta here (same observable contract: status:error, no result) while projecting an
        # allowlisted failure classification, so the Stage 5 deploy-stg diagnostic can distinguish an
        # LLM key / egress gap from a template defect. exc.error_log carries the inner node's
        # structured "NodeName: ExceptionClass" entry (never a message/body — the inner nodes record
        # type(exc).__name__); classify() re-narrows it to the allowlist as defense in depth.
        try:
            out: dict[str, Any] = super().execute(state)
            return out
        except SubgraphError as exc:
            # The inner agent failed and produced no partial output. Sent here because this is the
            # only place the outer run can say so: the inner node's own emit never reaches the
            # caller once merge_output is skipped.
            emit_progress(explain("subgraph_error"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ProcurementWorkflowGraphNode: SubgraphError"],
                "stg_error": stg_error.classify(exc.error_log),
            }

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        # Success path only (the framework raises on an inner error, handled in execute()): project
        # the assembled output → inherited result, the audit artifact, and status. Reducer-managed
        # fields are never projected inner → outer.
        payload = sub_result.get("output") or sub_result.get("result")
        return {
            "result": payload,
            "audit_metadata": sub_result.get("audit_metadata"),
            "status": sub_result.get("status"),
        }
