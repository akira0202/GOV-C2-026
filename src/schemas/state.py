"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

from typing import Any, NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Outer State for PublicProcurementSpecificationDraftAgent (Cat 2).

    Flat TypedDict — primitives / JSON-serializable types only (no
    Pydantic, dataclass, or arbitrary Python objects; msgpack-safe).

    Only outer-graph-specific fields are declared here. Framework fields
    are inherited from AgentState (which itself extends SubgraphContext)
    and are NOT redeclared:
      - user_input: str  — raw caller payload; JSON is passed as a string
      - validated_input  — set by the outer pre_process (input_validate)
      - result / formatted_output — terminal output; AgentBaseGraph.get_output()
        returns formatted_output or result
      - status, session_id, schema_version, response_metadata, and the
        reducer-managed node_history / error_log

    Cat 2 two-layer: the domain pipeline runs in the inner
    DomainWorkflowGraph (src/graph/domain_workflow_graph.py), which owns its
    own DomainWorkflowState holding the intermediate domain fields
    (normalized_requirements, compliance_flags, spec_draft, spec_with_clauses,
    clause_list, reviewer_checklist). The main GraphNode's merge_output()
    projects the inner output into the inherited `result` (plus audit_metadata
    below); node_history / error_log are reducer-managed and are NEVER
    projected inner -> outer.

    The final S-3-gated structured result is written to the inherited
    `result`. No credentials / InvocationContext / Pydantic in State.
    """

    # Audit metadata projected from the inner workflow (S-4 artifact).
    audit_metadata: NotRequired[dict[str, Any]]  # {data_hash, generated_at, kb_version,
    #  model_version, law_jurisdiction}

    # Allowlisted failure classification for the Stage 5 deploy-stg diagnostic — projected
    # from the inner error_log by merge_output on a failed run. "NodeName: ErrorClass" only;
    # never a raw error message, response, procurement input, or secret (src/services/stg_error).
    # Set when the input gate answered a request the caller can correct: the run completes with
    # this allow-listed code instead of a draft, and post_process releases the sentence the code
    # names. Never carries a request value.
    diagnostic_code: NotRequired[str]

    stg_error: NotRequired[str]
