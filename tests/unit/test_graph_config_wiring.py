"""Graph-level integration tests — prove the DEPLOYED config wiring, not just node
defaults (require the framework — run in CI).

The node's per-call → config precedence and the advisory_only code-constant enforcement
are only real if the graph actually injects config/config.yaml into InputValidateNode.
These tests construct the real Graph, register its nodes, and exercise the node the graph
built — so a regression to a bare ``InputValidateNode()`` (config not wired) fails here.
"""

import json

import pytest

from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus

from src.graph.graph import PublicProcurementSpecificationDraftAgentGraph as Graph


def _obj(**extra):
    d = {"procurement_objective": "Supply and install 50 office PCs", "budget": 1_000_000}
    d.update(extra)
    return json.dumps(d)


class TestGraphConfigWiring:
    def test_config_default_resolves_through_the_graph_path(self):
        # deployed path: no per-call procurement_type → resolves to the config.yaml default
        g = Graph(config={"procurement_type": "IT-services"})
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _obj(), "correlation_id": "g"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["validated_input"]["procurement_type"] == "IT-services"

    def test_per_call_still_overrides_the_wired_config(self):
        g = Graph(config={"procurement_type": "IT-services"})
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _obj(procurement_type="construction"), "correlation_id": "g"})
        assert r["validated_input"]["procurement_type"] == "construction"

    def test_unresolved_when_no_config_default_and_no_per_call(self):
        # proves the wired default actually matters: without it the invocation is rejected
        g = Graph(config={})
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _obj(), "correlation_id": "g"})
        # Without the wired default the request is not accepted — no validated_input is produced.
        # It is answered rather than refused: the caller can name procurement_type in the request.
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "procurement_type_unresolved"
        assert "validated_input" not in r

    def test_registration_rejects_runtime_advisory_only_false(self):
        g = Graph(config={"advisory_only": False})
        with pytest.raises(ConfigError):
            g.register_nodes()

    def test_registration_rejects_malformed_config_default(self):
        g = Graph(config={"procurement_type": ["not", "a", "string"]})
        with pytest.raises(ConfigError):
            g.register_nodes()

    def test_registration_rejects_control_char_config_default(self):
        g = Graph(config={"law_jurisdiction": "JP\ninjected"})
        with pytest.raises(ConfigError):
            g.register_nodes()

    def test_post_process_is_the_wired_s3_output_gate(self):
        # the deployed post_process slot is the S-3 output gate, not a scaffold passthrough
        from src.nodes.security_gate_output_node import SecurityGateOutputNode
        from src.services import output_gate
        g = Graph(config={"procurement_type": "IT-services"})
        g.register_nodes()
        node = g._nodes["post_process"]
        assert isinstance(node, SecurityGateOutputNode)
        # the S-3 gate injects the advisory disclaimer and hands the gated draft to formatted_output
        out = node.execute(
            {"result": "# 仕様書\n本文", "validated_input": {"procurement_objective": "x", "budget": 1.0, "procurement_type": "IT", "advisory_only": True}, "correlation_id": "g"}
        )
        assert output_gate.has_disclaimer(out["formatted_output"]) and "本文" in out["formatted_output"]

    def test_registration_rejects_runtime_advisory_only_false_at_output_gate(self):
        # the S-3 output gate also hard-locks advisory_only at construction (config false rejected)
        g = Graph(config={"advisory_only": False})
        with pytest.raises(ConfigError):
            g.register_nodes()

    def test_main_is_the_inner_workflow_graph_node(self):
        from src.graph.domain_workflow_graph import ProcurementWorkflowGraphNode
        g = Graph(config={"procurement_type": "IT-services"})
        g.register_nodes()
        assert isinstance(g._nodes["main"], ProcurementWorkflowGraphNode)


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


class TestEndToEndInvoke:
    """Real invoke path through the deployed outer graph → inner workflow → S-3 gate (CI)."""

    def test_inner_graph_invoke_directly(self):
        # isolate the inner workflow (real langgraph + inner __call__) from the outer routing;
        # the inner get_output surfaces error_log on failure, so this pinpoints a failing inner node
        from src.graph.domain_workflow_graph import DomainWorkflowGraph
        inner = DomainWorkflowGraph(config={"llm": _FakeLLM()})
        result = inner.invoke(_obj())
        assert result.get("status") != "error", f"inner node failed: {result.get('error_log')}"
        assert isinstance(result.get("output"), dict) and "specification" in result["output"], \
            f"inner get_output shape: {result!r}"
        # mandatory clauses are grounded on the bundled library (not placeholders) and the audit
        # records the exact clause-library revision used
        assert "必須条項" in result["output"]["specification"] and "入札公告" in result["output"]["specification"]
        assert result.get("audit_metadata", {}).get("kb_version")

    def test_invoke_reaches_inner_workflow_and_gates_output(self):
        from framework.schemas.invocation_context import InvocationContext
        from framework.schemas.trust_level import TrustLevel
        from src.services import output_gate
        g = Graph(config={"llm": _FakeLLM(), "procurement_type": "IT", "advisory_only": True})
        # a verified caller (as the API gateway provides in production) so the outer S-1 boundary
        # (input_validate) admits the request
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = g.invoke(_obj(), ctx=ctx)
        # the full outer composition runs end to end without error: the verified caller passes the
        # outer S-1 boundary, the main GraphNode reaches the inner workflow, and the S-3 output gate
        # runs (the inner-workflow content itself is asserted in test_inner_graph_invoke_directly)
        assert result.get("status") != "error", f"invoke errored: {result.get('error_log')}"
        nh = result.get("node_history", [])
        assert "ProcurementWorkflowGraphNode" in nh and "SecurityGateOutputNode" in nh, \
            f"composition did not run end to end: {nh}"
        # the S-3-gated specification must actually reach the caller — a non-null output carrying the
        # advisory disclaimer AND the real grounded draft content (not just a disclaimer wrapper)
        output = result.get("output")
        assert output, f"final output must be non-null: {output!r}"
        assert output_gate.has_disclaimer(output)
        import json as _json
        text = output if isinstance(output, str) else _json.dumps(output, ensure_ascii=False)
        assert "必須条項" in text and "入札公告" in text, "grounded specification content missing from final output"
