# GOV-C2-026 — Unit Tests: InputValidateNode (outer pre_process / S-2 gate)

import json

import pytest

from src.nodes.input_validate_node import InputValidateNode
from src.services.failure_message import explain
from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus


def _payload(objective="Supply and install 50 office PCs", budget=5_000_000, **extra):
    p = {"procurement_objective": objective, "budget": budget}
    p.update(extra)
    return json.dumps(p)


class TestPrecedenceAndSuccess:
    def test_valid_json_with_config_fallback(self):
        node = InputValidateNode(node_config={"procurement_type": "IT", "law_jurisdiction": "地方自治法"})
        result = node.execute({"user_input": _payload(), "correlation_id": "t1"})
        assert result["status"] == AgentStatus.SUCCESS.value
        vi = result["validated_input"]
        assert vi["procurement_type"] == "IT"                 # from config default
        assert vi["law_jurisdiction"] == "地方自治法"
        assert vi["budget"] == 5_000_000.0
        assert vi["advisory_only"] is True

    def test_per_call_overrides_config(self):
        node = InputValidateNode(node_config={"procurement_type": "IT"})
        result = node.execute({"user_input": _payload(procurement_type="工事"), "correlation_id": "t2"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"]["procurement_type"] == "工事"   # per-call wins

    def test_optional_passthrough_and_structured_only(self):
        node = InputValidateNode(node_config={"procurement_type": "役務"})
        result = node.execute({
            "user_input": _payload(timeline="2025-Q4", technical_requirements=["ISO27001"], personal_id="123-45-6789"),
            "correlation_id": "t3",
        })
        assert result["status"] == AgentStatus.SUCCESS.value
        vi = result["validated_input"]
        assert vi["timeline"] == "2025-Q4"
        assert vi["technical_requirements"] == ["ISO27001"]
        # structured-only: an unexpected (raw-PII-bearing) field is NOT carried through
        assert "personal_id" not in vi

    def test_legitimate_vendor_name_not_blocked(self):
        # A legitimate vendor/person name in the NL objective must NOT be false-positive-blocked.
        node = InputValidateNode(node_config={"procurement_type": "物品"})
        result = node.execute({"user_input": _payload(objective="山田商事株式会社より事務用品を調達"), "correlation_id": "t4"})
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_input_validated_trace_emitted_once(self, monkeypatch):
        events = []
        monkeypatch.setattr("src.nodes.input_validate_node.emit_trace_event",
                            lambda et, payload, state: events.append(et))
        node = InputValidateNode(node_config={"procurement_type": "IT"})
        result = node.execute({"user_input": _payload(), "correlation_id": "t5"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert events.count("input_validated") == 1


class TestRejections:
    def setup_method(self):
        self.node = InputValidateNode(node_config={"procurement_type": "IT"})

    def _ex(self, ui):
        return self.node.execute({"user_input": ui, "correlation_id": "r"})

    def test_missing_objective(self):
        r = self._ex(json.dumps({"budget": 1000}))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"].startswith(("missing_objective", "objective_"))
        assert "validated_input" not in r

    def test_missing_budget(self):
        r = self._ex(json.dumps({"procurement_objective": "x"}))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "budget" in explain(r["diagnostic_code"])

    def test_non_positive_budget(self):
        r = self._ex(_payload(budget=0))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "budget" in explain(r["diagnostic_code"])

    def test_procurement_type_unresolved(self):
        node = InputValidateNode()  # no config default
        r = node.execute({"user_input": _payload(), "correlation_id": "r"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "procurement_type" in explain(r["diagnostic_code"])

    def test_malformed_json(self):
        r = self._ex('{"procurement_objective": ')
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "malformed"
        assert "not valid JSON" in explain(r["diagnostic_code"])

    def test_non_object_json(self):
        r = self._ex(json.dumps([1, 2, 3]))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "JSON object" in explain(r["diagnostic_code"])

    def test_procurement_type_control_char_injection(self):
        r = self._ex(_payload(procurement_type="IT\nDROP"))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "procurement_type" in explain(r["diagnostic_code"])

    def test_procurement_type_non_str_not_coerced(self):
        # Untrusted per-call procurement_type of a non-str type is rejected, never str()-coerced.
        r = self._ex(_payload(procurement_type=12345))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "procurement_type" in explain(r["diagnostic_code"])

    def test_law_jurisdiction_non_str_not_coerced(self):
        r = self._ex(_payload(procurement_type="IT", law_jurisdiction={"x": 1}))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "law_jurisdiction" in explain(r["diagnostic_code"])


class TestAdvisoryOnly:
    def test_config_false_raises_configerror_at_load(self):
        with pytest.raises(ConfigError):
            InputValidateNode(node_config={"advisory_only": False})

    def test_config_true_ok(self):
        node = InputValidateNode(node_config={"advisory_only": True, "procurement_type": "IT"})
        assert node is not None

    def test_payload_false_is_ignored_not_disabled(self):
        node = InputValidateNode(node_config={"procurement_type": "IT"})
        result = node.execute({"user_input": _payload(advisory_only=False), "correlation_id": "a"})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"]["advisory_only"] is True   # forced constant, not disabled


class TestConfigDefaults:
    """Config-supplied single-line-key defaults are validated at construction, not
    coerced — the same strict typing applied to caller values."""

    @pytest.mark.parametrize("bad", [["a", "b"], {"x": 1}, 42, True])
    def test_non_string_config_default_raises(self, bad):
        with pytest.raises(ConfigError):
            InputValidateNode(node_config={"procurement_type": bad})

    def test_control_char_config_default_raises(self):
        with pytest.raises(ConfigError):
            InputValidateNode(node_config={"law_jurisdiction": "JP\ninjected"})

    def test_over_length_config_default_raises(self):
        with pytest.raises(ConfigError):
            InputValidateNode(node_config={"procurement_type": "x" * 100})

    def test_valid_string_config_default_used(self):
        node = InputValidateNode(node_config={"procurement_type": "IT-services"})
        r = node.execute({"user_input": _payload(), "correlation_id": "c"})
        assert r["validated_input"]["procurement_type"] == "IT-services"

    def test_blank_config_default_treated_as_absent(self):
        node = InputValidateNode(node_config={"procurement_type": "   "})
        r = node.execute({"user_input": _payload(), "correlation_id": "c"})
        assert r["status"] == AgentStatus.SUCCESS.value  # no usable default → unresolved


class TestS2Gate:
    def setup_method(self):
        self.node = InputValidateNode(node_config={"procurement_type": "IT"})

    def test_s2_rejects_non_str_type(self):
        for bad in (b"binary", 123, {"a": 1}, ["l"]):
            gated = self.node._extra_security_gate_input({"user_input": bad, "correlation_id": "s2"})
            assert gated["status"] == AgentStatus.ERROR.value, f"{type(bad).__name__} should be rejected"
            assert "validated_input" not in gated

    def test_s2_rejects_nul_byte(self):
        gated = self.node._extra_security_gate_input({"user_input": "ok\x00bin", "correlation_id": "s2"})
        assert gated["status"] == AgentStatus.ERROR.value
        assert "NUL" in gated["error_log"][0]

    def test_s2_answers_oversized_without_parsing_it(self):
        """The ceiling is a limit the caller can work within, so it is answered rather than
        refused. The answer carries no user_input, and execute() returns on seeing the code — so an
        oversized body is still never parsed."""
        node = InputValidateNode(node_config={"procurement_type": "IT", "max_input_chars": 100})
        gated = node._extra_security_gate_input({"user_input": "x" * 500, "correlation_id": "s2"})
        assert gated["status"] == AgentStatus.SUCCESS.value
        assert gated["diagnostic_code"] == "s2_oversized"
        assert set(gated.keys()) == {"status", "diagnostic_code"}
        after = node.execute(gated)
        assert after == {"status": AgentStatus.SUCCESS.value, "diagnostic_code": "s2_oversized"}
        assert "validated_input" not in after

    def test_s2_answer_does_not_let_a_body_be_parsed_afterwards(self):
        """Guard the short-circuit itself: if execute() stopped honouring the code, the oversized
        body left in state would be parsed after the gate had already answered."""
        node = InputValidateNode(node_config={"procurement_type": "IT", "max_input_chars": 100})
        after = node.execute({"user_input": _payload(), "diagnostic_code": "s2_oversized", "correlation_id": "s2"})
        assert "validated_input" not in after
        assert after["diagnostic_code"] == "s2_oversized"

    def test_s2_passes_clean_text_unchanged(self):
        state = {"user_input": _payload(), "correlation_id": "s2"}
        assert self.node._extra_security_gate_input(state) is state

    def test_s2_does_not_coerce_binary(self):
        gated = self.node._extra_security_gate_input({"user_input": b"\x00abc", "correlation_id": "s2"})
        assert gated["status"] == AgentStatus.ERROR.value
        assert "must be text" in gated["error_log"][0]


class TestCallPathBoundary:
    """PB-style: via the node's real __call__ path, an S-2 reject short-circuits before
    execute() so validated_input is never produced (framework runs the @final gate)."""

    def test_s2_reject_via_call_does_not_invoke_execute(self, monkeypatch):
        # Prove the S-2 gate short-circuits: execute() must NOT run on an S-2-invalid
        # payload. Asserting only that validated_input is absent is insufficient —
        # execute() also omits it for bytes, so that would pass for the wrong reason.
        calls = []
        orig_execute = InputValidateNode.execute

        def spy(self, state, _o=orig_execute):
            calls.append(True)
            return _o(self, state)

        monkeypatch.setattr(InputValidateNode, "execute", spy)
        node = InputValidateNode()
        state = {
            "user_input": b"\x00binary",  # non-text → S-2 reject in the gate, before execute()
            "caller_trust_level": InputValidateNode.required_trust_level.value,
            "correlation_id": "call-pb",
            "session_id": "s", "thread_id": "t", "trace_id": "tr",
        }
        result = node(state)
        assert calls == []  # execute() was NOT invoked — the S-2 gate short-circuited
        assert "validated_input" not in result
        assert result.get("status") == AgentStatus.ERROR.value


class TestOptionalContract:
    """Optional payload fields must satisfy their documented typed contract, not just
    be JSON-safe: timeline is a bounded control-char-free string; technical_requirements
    is bounded NL text (a string or list of strings)."""

    def setup_method(self):
        self.node = InputValidateNode(node_config={"procurement_type": "IT"})

    def _ex(self, **opt):
        return self.node.execute(
            {"user_input": _payload(**opt), "correlation_id": "o"}
        )

    def test_timeline_non_string_rejected(self):
        r = self._ex(timeline=["Q4"])
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "optional_contract"

    def test_timeline_control_char_rejected(self):
        r = self._ex(timeline="2025\nHACK")
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "optional_contract"

    def test_technical_requirements_dict_rejected(self):
        r = self._ex(technical_requirements={"x": 1})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "optional_contract"

    def test_technical_requirements_list_non_str_item_rejected(self):
        r = self._ex(technical_requirements=["ok", 123])
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "optional_contract"

    def test_technical_requirements_multiline_string_ok(self):
        r = self._ex(technical_requirements="requirement line 1\nrequirement line 2")
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "line 2" in r["validated_input"]["technical_requirements"]

    def test_technical_requirements_string_control_char_injection_rejected(self):
        # docs/02 "reject injection in NL requirement text" — a stray control char
        # (other than tab/newline/CR) is structural injection, rejected.
        r = self._ex(technical_requirements="ok requirement\x07evil")
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "optional_contract"

    def test_technical_requirements_list_item_control_char_injection_rejected(self):
        r = self._ex(technical_requirements=["fine", "bad\x1b[2Jclear"])
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "optional_contract"


class TestRecursionSafety:
    """Deeply nested JSON must fail-closed (ERROR), never raise RecursionError."""

    def test_deep_nesting_does_not_crash(self):
        node = InputValidateNode()
        deep = "{" * 2000 + "}" * 2000  # pathologically nested JSON tokens
        result = node.execute({"user_input": deep, "correlation_id": "d"})
        assert result["status"] == AgentStatus.SUCCESS.value


class TestObjectiveBound:
    def test_procurement_objective_over_length_rejected(self):
        node = InputValidateNode(node_config={"procurement_type": "IT"})
        r = node.execute({"user_input": _payload(objective="x" * 30_000), "correlation_id": "b"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"].startswith(("missing_objective", "objective_"))

    def test_procurement_objective_multiline_ok(self):
        node = InputValidateNode(node_config={"procurement_type": "IT"})
        r = node.execute(
            {"user_input": _payload(objective="Supply 50 laptops.\nDelivery in Q3.\n- 50 units"),
             "correlation_id": "b"}
        )
        assert r["status"] == AgentStatus.SUCCESS.value

    def test_procurement_objective_control_char_injection_rejected(self):
        # docs/02 "reject injection in NL requirement text" — structural screen on the
        # primary NL field; multi-line stays valid, a stray control char is rejected.
        node = InputValidateNode(node_config={"procurement_type": "IT"})
        r = node.execute({"user_input": _payload(objective="obj\x01injected"), "correlation_id": "b"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"].startswith(("missing_objective", "objective_"))
