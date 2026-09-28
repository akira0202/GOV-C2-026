"""Unit tests for the GOV generation / output nodes (require the framework — run in CI)."""

import json

import pytest

from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.requirements_normalize_node import RequirementsNormalizeNode
from src.nodes.specification_draft_node import SpecificationDraftNode
from src.nodes.security_gate_output_node import SecurityGateOutputNode

import src.nodes.requirements_normalize_node as rn_mod
import src.nodes.security_gate_output_node as sg_mod
from src.services import output_gate

_NORM_JSON = json.dumps(
    {
        "purpose": "庁舎ネットワーク更改",
        "scope": "全庁 LAN",
        "technical_requirements": ["10Gbps 対応スイッチ"],
        "evaluation_criteria": ["疎通試験合格"],
        "timeline": "2025-Q3",
        "budget_ceiling": "5,000,000 円",
    }
)


class _NormalizeLLM:
    def complete(self, messages, state):
        assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
        return _NORM_JSON


class _SpecLLM:
    def complete(self, messages, state):
        assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
        return "# 調達仕様書\n\n## 調達目的\n庁舎ネットワーク更改"


class _MalformedLLM:
    def complete(self, messages, state):
        return "not a valid json object"


class _RaisingLLM:
    def complete(self, messages, state):
        raise TimeoutError("llm timeout")


def _validated():
    return {"procurement_objective": "庁舎LAN更改", "budget": 5000000, "procurement_type": "IT"}


class TestContract:
    def test_no_arg_constructible(self):  # PB-6
        for nc in (RequirementsNormalizeNode, SpecificationDraftNode, SecurityGateOutputNode):
            assert nc() is not None

    def test_trust_levels(self):
        # inner LLM nodes are ANONYMOUS (inside the outer boundary); the outer S-3 gate is
        # VERIFIED_EXTERNAL (it runs in the outer graph where caller trust is present)
        for nc in (RequirementsNormalizeNode, SpecificationDraftNode):
            assert nc.required_trust_level is TrustLevel.ANONYMOUS
        assert SecurityGateOutputNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL


class TestRequirementsNormalize:
    def test_success_delta_only(self):
        d = RequirementsNormalizeNode(llm=_NormalizeLLM()).execute(
            {"validated_input": _validated(), "correlation_id": "t"}
        )
        assert set(d.keys()) == {"normalized_requirements", "status"}
        assert d["status"] == AgentStatus.SUCCESS.value
        assert d["normalized_requirements"]["purpose"] == "庁舎ネットワーク更改"

    def test_llm_unavailable_propagates_error(self):
        d = RequirementsNormalizeNode(llm=None).execute({"validated_input": _validated(), "correlation_id": "t"})
        assert d["status"] == AgentStatus.ERROR.value and "normalized_requirements" not in d

    def test_malformed_llm_propagates_error(self):
        d = RequirementsNormalizeNode(llm=_MalformedLLM()).execute(
            {"validated_input": _validated(), "correlation_id": "t"}
        )
        assert d["status"] == AgentStatus.ERROR.value and "normalized_requirements" not in d

    def test_llm_exception_propagates_error(self):
        d = RequirementsNormalizeNode(llm=_RaisingLLM()).execute(
            {"validated_input": _validated(), "correlation_id": "t"}
        )
        assert d["status"] == AgentStatus.ERROR.value

    def test_missing_input_fails_closed(self):
        assert (
            RequirementsNormalizeNode(llm=_NormalizeLLM()).execute({"correlation_id": "t"})["status"]
            == AgentStatus.ERROR.value
        )

    def test_emits_domain_event(self, monkeypatch):
        seen = []
        monkeypatch.setattr(rn_mod, "emit_trace_event", lambda et, p, s=None: seen.append(et))
        RequirementsNormalizeNode(llm=_NormalizeLLM()).execute({"validated_input": _validated(), "correlation_id": "t"})
        assert "requirements_normalized" in seen

    def test_no_raw_body_or_llm_in_delta(self):
        d = RequirementsNormalizeNode(llm=_NormalizeLLM()).execute(
            {"validated_input": _validated(), "correlation_id": "t"}
        )
        json.dumps(d)  # JSON-serializable → no LLM client / non-serializable object
        assert "庁舎LAN更改" not in json.dumps(d)  # raw caller objective not echoed into the delta


class TestSpecificationDraft:
    def test_success_delta_only_preserves_flags(self):
        state = {
            "normalized_requirements": json.loads(_NORM_JSON),
            "compliance_flags": [{"rule": "missing_scope", "severity": "Medium", "field": "scope", "message": "x"}],
            "correlation_id": "t",
        }
        d = SpecificationDraftNode(llm=_SpecLLM()).execute(state)
        assert set(d.keys()) == {"spec_draft", "status"}  # delta-only: compliance_flags untouched in state
        assert d["status"] == AgentStatus.SUCCESS.value and "調達仕様書" in d["spec_draft"]

    def test_llm_unavailable_propagates_error(self):
        d = SpecificationDraftNode(llm=None).execute(
            {"normalized_requirements": json.loads(_NORM_JSON), "correlation_id": "t"}
        )
        assert d["status"] == AgentStatus.ERROR.value and "spec_draft" not in d

    def test_llm_exception_propagates_error(self):
        d = SpecificationDraftNode(llm=_RaisingLLM()).execute(
            {"normalized_requirements": json.loads(_NORM_JSON), "correlation_id": "t"}
        )
        assert d["status"] == AgentStatus.ERROR.value

    def test_missing_input_fails_closed(self):
        assert (
            SpecificationDraftNode(llm=_SpecLLM()).execute({"correlation_id": "t"})["status"] == AgentStatus.ERROR.value
        )


class TestSecurityGateOutput:
    def test_config_advisory_only_false_rejected(self):
        with pytest.raises(ConfigError):
            SecurityGateOutputNode({"advisory_only": False})

    def test_config_advisory_only_true_ok(self):
        assert SecurityGateOutputNode({"advisory_only": True}) is not None

    def test_execute_writes_disclaimer_to_formatted_output(self):
        # the gated draft (disclaimer guaranteed) must reach the caller via formatted_output
        # (get_output = formatted_output or result)
        out = SecurityGateOutputNode().execute(
            {"result": "# 仕様書\n本文", "validated_input": {"procurement_objective": "x", "budget": 1.0, "procurement_type": "IT", "advisory_only": True}, "correlation_id": "t"}
        )
        assert out["status"] == AgentStatus.SUCCESS.value
        assert output_gate.has_disclaimer(out["formatted_output"])
        assert "本文" in out["formatted_output"]  # the real draft content is preserved, not lost

    def test_execute_blocks_advisory_only_tamper(self):
        out = SecurityGateOutputNode().execute(
            {"result": "x", "validated_input": {"procurement_objective": "x", "budget": 1.0, "procurement_type": "IT", "advisory_only": True}, "advisory_only": False, "correlation_id": "t"}
        )
        assert out["status"] == AgentStatus.ERROR.value and "formatted_output" not in out  # blocked, no output

    def test_execute_blocks_when_the_carried_posture_is_gone(self):
        """The posture the gate reads is the one input_validate writes, inside validated_input."""
        out = SecurityGateOutputNode().execute(
            {"result": "x", "validated_input": {"procurement_objective": "x"}, "correlation_id": "t"}
        )
        assert out["status"] == AgentStatus.ERROR.value and "formatted_output" not in out

    def test_execute_emits_output_gated_once(self, monkeypatch):
        seen = []
        monkeypatch.setattr(sg_mod, "emit_trace_event", lambda et, p, s=None: seen.append(et))
        SecurityGateOutputNode().execute({"result": "x", "correlation_id": "t"})
        assert seen.count("output_gated") == 1


class TestSerialContract:
    """The generation nodes chain with the deterministic compliance rule engine (graph wiring is
    a follow-on step)."""

    def test_normalize_then_compliance_then_draft(self):
        from src.services import compliance_rules

        # normalize (LLM) -> deterministic compliance check -> specification draft (LLM)
        norm = RequirementsNormalizeNode(llm=_NormalizeLLM()).execute(
            {"validated_input": _validated(), "correlation_id": "e"}
        )
        flags = compliance_rules.check(norm["normalized_requirements"], law_jurisdiction="会計法")
        spec = SpecificationDraftNode(llm=_SpecLLM()).execute(
            {
                "normalized_requirements": norm["normalized_requirements"],
                "compliance_flags": flags,
                "correlation_id": "e",
            }
        )
        assert norm["status"] == AgentStatus.SUCCESS.value and spec["status"] == AgentStatus.SUCCESS.value
        assert isinstance(spec["spec_draft"], str) and spec["spec_draft"]


class TestDraftLLMAdapter:
    """The keyless runtime adapter resolves AZURE_OPENAI_* per-request and fails closed when it is not
    provisioned — it never silently emits an unauthenticated call."""

    def test_no_arg_constructible(self):  # keyless: safe to build at startup, no key read
        from src.services.llm import AzureOpenAIDraftLLM

        assert AzureOpenAIDraftLLM() is not None

    def test_fails_closed_without_bound_secret(self):
        from src.services.llm import AzureOpenAIDraftLLM

        with pytest.raises(Exception):
            AzureOpenAIDraftLLM().complete([{"role": "system", "content": "x"}], {"correlation_id": "t"})


class TestClauseGroundingNode:
    """The clause-inject node is grounded on the bundled library by default and fails closed when a
    mandatory clause is ungrounded."""

    def test_bundled_grounds_and_projects_kb_version(self):
        from src.nodes.compliance_clause_inject_node import ComplianceClauseInjectNode

        d = ComplianceClauseInjectNode().execute(
            {"spec_draft": "# 仕様書\n本文", "validated_input": {"law_jurisdiction": "会計法"}, "correlation_id": "t"}
        )
        assert d["status"] == AgentStatus.SUCCESS.value
        assert all(not c["missing"] for c in d["clause_list"]) and d["kb_version"]

    def test_ungrounded_override_fails_closed(self):
        from src.nodes.compliance_clause_inject_node import ComplianceClauseInjectNode

        # a deployment override missing required clause text must fail closed, never an ungrounded draft
        node = ComplianceClauseInjectNode(
            {"compliance_clause_library": {"clauses": {"nyusatsu_koukoku": {"text": "入札公告", "source": "x"}}}}
        )
        d = node.execute(
            {"spec_draft": "# 仕様書", "validated_input": {"law_jurisdiction": "会計法"}, "correlation_id": "t"}
        )
        assert d["status"] == AgentStatus.ERROR.value
