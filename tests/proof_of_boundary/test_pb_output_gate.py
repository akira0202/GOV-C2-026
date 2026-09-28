# PB: S-3 Output Security Gate — boundary output-protection proof
#
# Proves the S-3 advisory output gate at the framework BOUNDARY (BaseNode.__call__),
# exercising the deployed post_process node rather than a helper in isolation:
#  - the advisory disclaimer is node-owned and injected into EVERY output (string or
#    dict), never dependent on the upstream LLM draft remaining intact;
#  - an advisory_only value tampered to false BLOCKS the output (observed via both the
#    ERROR status and the emitted output_gated{blocked:true} event — a result-absence
#    assertion alone would not prove the block branch fired);
#  - the advisory posture is a code-side constant: a config advisory_only:false is
#    rejected at construction (fail-closed), it is not a disable toggle;
#  - exactly one output_gated S-4 event is emitted per invocation.

import pytest

from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus

from src.nodes.security_gate_output_node import SecurityGateOutputNode
from src.services import output_gate


def _call_state(result, **extra):
    """A minimal __call__ state that passes the S-1 trust gate for SecurityGateOutputNode."""
    state = {
        "result": result,
        # The advisory posture the gate checks lives INSIDE validated_input — that is what
        # input_validate actually writes, and a state without it is not a state this gate can ever
        # see on the real path. Building it here keeps the fixture honest.
        "validated_input": {
            "procurement_objective": "Supply and install 50 office PCs",
            "budget": 5_000_000.0,
            "procurement_type": "IT",
            "advisory_only": True,
        },
        "caller_trust_level": SecurityGateOutputNode.required_trust_level.value,
        "correlation_id": "pb-s3",
        "session_id": "s",
        "thread_id": "t",
        "trace_id": "tr",
    }
    state.update(extra)
    return state


def _spy_events(monkeypatch):
    """Capture (event_type, payload) for every emit_trace_event from the S-3 node."""
    events: list[tuple] = []
    monkeypatch.setattr(
        "src.nodes.security_gate_output_node.emit_trace_event",
        lambda event_type, payload, _state, _e=events: _e.append((event_type, payload)),
    )
    return events


class TestS3OutputGateBoundaryProtection:
    def test_advisory_only_tamper_blocked_at_boundary(self, monkeypatch):
        events = _spy_events(monkeypatch)
        result = SecurityGateOutputNode()(_call_state("a procurement spec draft", advisory_only=False))
        # the block branch fired (mechanism), not merely a missing output
        assert any(e[0] == "output_gated" and e[1].get("blocked") is True for e in events)
        assert result.get("status") == AgentStatus.ERROR.value
        assert "formatted_output" not in result

    @pytest.mark.parametrize(
        "tampered",
        [
            {"procurement_objective": "x", "budget": 1.0, "procurement_type": "IT", "advisory_only": False},
            {"procurement_objective": "x", "budget": 1.0, "procurement_type": "IT", "advisory_only": "true"},
            {"procurement_objective": "x", "budget": 1.0, "procurement_type": "IT"},  # dropped entirely
            {},
            "advisory_only: true",  # reshaped to a string
            None,
        ],
    )
    def test_the_posture_is_checked_where_it_is_actually_carried(self, monkeypatch, tampered):
        """input_validate writes advisory_only INSIDE validated_input, and nothing writes a
        top-level state["advisory_only"]. A gate keyed on that top-level name would read a field
        that is always absent and could never fire. These cases tamper with the field the run
        really carries — dropping it, reshaping it, or making it something other than true — and
        each must be blocked."""
        events = _spy_events(monkeypatch)
        result = SecurityGateOutputNode()(_call_state("a procurement spec draft", validated_input=tampered))
        assert any(e[0] == "output_gated" and e[1].get("blocked") is True for e in events)
        assert result.get("status") == AgentStatus.ERROR.value
        assert "formatted_output" not in result

    def test_an_intact_posture_is_not_blocked(self):
        """Negative control — the canonical validated_input must pass, or the test above would
        pass for the wrong reason."""
        result = SecurityGateOutputNode()(_call_state("a procurement spec draft"))
        assert result.get("status") == AgentStatus.SUCCESS.value

    def test_disclaimer_always_injected_for_string_draft(self):
        node = SecurityGateOutputNode()
        result = node(_call_state("Specification section 1: scope and requirements."))
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert output_gate.has_disclaimer(result["formatted_output"])
        assert output_gate.ADVISORY_DISCLAIMER in result["formatted_output"]

    def test_disclaimer_injected_for_dict_result(self):
        node = SecurityGateOutputNode()
        result = node(_call_state({"spec": "body", "clauses": ["c1"]}))
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert output_gate.has_disclaimer(result["formatted_output"])
        assert result["formatted_output"]["disclaimer"] == output_gate.ADVISORY_DISCLAIMER

    def test_output_gated_emitted_once_per_invocation(self, monkeypatch):
        events = _spy_events(monkeypatch)
        SecurityGateOutputNode()(_call_state("draft body"))
        assert [e[0] for e in events].count("output_gated") == 1


class TestS3ConstructionFailClosed:
    def test_config_advisory_only_false_rejected_at_construction(self):
        with pytest.raises(ConfigError):
            SecurityGateOutputNode(node_config={"advisory_only": False})

    def test_config_advisory_only_true_ok(self):
        assert SecurityGateOutputNode(node_config={"advisory_only": True}) is not None
