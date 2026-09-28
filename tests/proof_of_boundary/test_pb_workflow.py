# PB workflow: end-to-end boundary tests for the approved workflow (outer Graph.invoke).
#
# Proves at the deployed outer boundary: (1) the required human-review posture is surfaced to the
# caller — the advisory disclaimer is present and the reviewer checklist marks every item
# human-validation-required, and the always-on audit_metadata artifact reaches the caller; and
# (2) invalid / partial input fails closed (error_strategy=propagate — no partial output).
# Synthetic inputs only; the LLM is faked (no live model call).

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import PublicProcurementSpecificationDraftAgentGraph as Graph
from src.services import output_gate
from src.services.failure_message import explain


def _obj(**extra):
    d = {"procurement_objective": "Supply and install 50 office PCs", "budget": 1_000_000}
    d.update(extra)
    return json.dumps(d)


class _FakeLLM:
    """One adapter serves both LLM nodes; branches on the system task."""

    def complete(self, messages, state):
        system = messages[0]["content"]
        if "structured schema" in system:  # requirements_normalize
            return json.dumps({"purpose": "庁舎ネットワーク更改", "scope": "全庁 LAN",
                               "technical_requirements": ["10Gbps 対応スイッチ"],
                               "evaluation_criteria": ["疎通試験合格"], "timeline": "2025-Q3",
                               "budget_ceiling": "5,000,000 円"})
        return "# 調達仕様書\n\n## 調達目的\n庁舎ネットワーク更改"  # specification_draft


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _graph():
    return Graph(config={"llm": _FakeLLM(), "procurement_type": "IT", "advisory_only": True})


class _CredentialDraftLLM:
    """requirements_normalize returns a clean schema; the specification draft (downstream-generated
    output) embeds a credential-shaped token, so the framework @final S-3 credential scan trips on
    the specification_draft node's return. AKIAIOSFODNN7EXAMPLE is the public AWS documentation
    example key, not a live secret."""

    def complete(self, messages, state):
        system = messages[0]["content"]
        if "structured schema" in system:  # requirements_normalize
            return json.dumps({"purpose": "庁舎ネットワーク更改", "scope": "全庁 LAN",
                               "technical_requirements": ["10Gbps 対応スイッチ"],
                               "evaluation_criteria": ["疎通試験合格"], "timeline": "2025-Q3",
                               "budget_ceiling": "5,000,000 円"})
        # specification_draft — a normal-looking draft whose body carries the credential-shaped token
        return "# 調達仕様書\n\n## 調達目的\n庁舎ネットワーク更改\n\n## 補足\nAKIAIOSFODNN7EXAMPLE\n"


class TestWorkflowHumanReviewEscalation:
    def test_human_review_posture_surfaced_to_caller(self):
        result = _graph().invoke(_obj(), ctx=_ctx())
        assert result.get("status") != "error", f"invoke errored: {result.get('error_log')}"
        output = result.get("output")
        assert output, f"final output must reach the caller: {output!r}"
        # the advisory (draft-for-human-review) disclaimer is always injected
        assert output_gate.has_disclaimer(output)
        # every reviewer-checklist item is marked human-validation-required
        checklist = output.get("reviewer_checklist") if isinstance(output, dict) else None
        assert isinstance(checklist, list) and checklist, f"reviewer_checklist must be present: {output!r}"
        assert all(item.get("human_validation_required") is True for item in checklist)
        # the always-on audit_metadata artifact reaches the caller (get_output surfaces it)
        am = result.get("audit_metadata")
        assert isinstance(am, dict) and am.get("kb_version"), f"audit_metadata must reach the caller: {am!r}"
        # composition ran end to end, in order: inner workflow node before the outer S-3 gate
        nh = result.get("node_history", [])
        assert "ProcurementWorkflowGraphNode" in nh and "SecurityGateOutputNode" in nh, f"composition: {nh}"
        assert nh.index("ProcurementWorkflowGraphNode") < nh.index("SecurityGateOutputNode"), f"order: {nh}"


class TestACorrectableRequestIsAnsweredEndToEnd:
    """A request the caller can correct is answered through the compiled graph: the turn completes
    carrying the code and the sentence, and no draft — not even an empty one — is produced."""

    def test_malformed_input_is_answered_with_a_notice(self):
        result = _graph().invoke("{ this is not valid json", ctx=_ctx())
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result.get("diagnostic_code") == "malformed"
        assert result.get("output") == explain("malformed")

    def test_missing_required_field_is_answered(self):
        # a valid JSON object but missing the mandatory budget
        result = _graph().invoke(json.dumps({"procurement_objective": "Supply 50 PCs"}), ctx=_ctx())
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result.get("diagnostic_code") == "missing_budget"
        assert result.get("output") == explain("missing_budget")

    def test_the_workflow_does_not_run_on_an_answered_request(self):
        """audit_metadata is written by the last inner node. Its absence is how this test shows the
        inner workflow was skipped rather than run and discarded."""
        result = _graph().invoke("{ this is not valid json", ctx=_ctx())
        assert result.get("audit_metadata") is None

    def test_a_refusal_this_agent_makes_still_fails_closed(self):
        """The screen that decides whether the body is a document at all keeps refusing: a NUL byte
        is not a shape a caller is invited to correct."""
        result = _graph().invoke("clean text\x00smuggled", ctx=_ctx())
        assert result.get("status") == AgentStatus.ERROR.value
        assert not result.get("output")
        assert result.get("diagnostic_code") is None


class TestS3CredentialBlockEndToEnd:
    """PB: the framework baseline S-3 credential scan BLOCKS end-to-end. A credential-
    shaped token in the LLM-generated draft trips the @final _security_gate_output on the
    specification_draft node's return; under error_strategy=propagate the outer invoke fails closed
    with no output, and the credential body never reaches a caller-facing channel. BLOCK is the
    approved posture — there is no downstream redaction-to-success, because baseline S-3 runs before
    and independent of the domain output gate."""

    def _credential_graph(self):
        return Graph(config={"llm": _CredentialDraftLLM(), "procurement_type": "IT", "advisory_only": True})

    def test_credential_in_generated_draft_blocks_with_no_output(self):
        result = self._credential_graph().invoke(_obj(), ctx=_ctx())
        # fail-closed BLOCK, not a redacted success
        assert result.get("status") == AgentStatus.ERROR.value
        assert result.get("output") is None
        # the credential body must not reach any caller-facing channel (result / stg_error / error_log)
        surfaced = json.dumps(result, ensure_ascii=False, default=str)
        assert "AKIAIOSFODNN7EXAMPLE" not in surfaced
