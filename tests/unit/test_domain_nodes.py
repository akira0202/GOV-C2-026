"""Unit tests for the GOV inner deterministic nodes (require the framework — run in CI)."""

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.input_hydrate_node import InputHydrateNode
from src.nodes.procurement_law_compliance_check_node import ProcurementLawComplianceCheckNode
from src.nodes.compliance_clause_inject_node import ComplianceClauseInjectNode
from src.nodes.review_checklist_generate_node import ReviewChecklistGenerateNode

import src.nodes.procurement_law_compliance_check_node as cc_mod

_ALL = [InputHydrateNode, ProcurementLawComplianceCheckNode,
        ComplianceClauseInjectNode, ReviewChecklistGenerateNode]

_VALIDATED = {"procurement_objective": "庁舎ネットワーク更改", "budget": 5000000,
              "procurement_type": "IT", "advisory_only": True, "law_jurisdiction": "地方自治法"}
_BAD_REQ = {"purpose": "", "technical_requirements": [], "evaluation_criteria": [], "timeline": ""}


class TestContract:
    def test_all_no_arg_constructible(self):  # PB-6
        for nc in _ALL:
            assert nc() is not None

    def test_all_inner_anonymous(self):
        # inner nodes run INSIDE the outer VERIFIED_EXTERNAL S-1 boundary (input_validate); the
        # framework does not propagate caller trust into the inner graph, so inner nodes are
        # ANONYMOUS (declared explicitly) — VERIFIED_EXTERNAL here would be unreachable.
        for nc in _ALL:
            assert nc.required_trust_level is TrustLevel.ANONYMOUS


class TestEndToEnd:
    def _run(self):
        state = {"user_input": json.dumps(_VALIDATED), "correlation_id": "e"}
        state.update(InputHydrateNode().execute(state))
        state["normalized_requirements"] = _BAD_REQ
        state["spec_draft"] = "# 仕様書\n本文ドラフト"
        state.update(ProcurementLawComplianceCheckNode().execute(state))
        # no config override → the node loads the bundled base clause library (grounded)
        state.update(ComplianceClauseInjectNode().execute(state))
        state.update(ReviewChecklistGenerateNode().execute(state))
        return state

    def test_full_flow(self):
        s = self._run()
        assert s["status"] == AgentStatus.SUCCESS.value
        assert len(s["compliance_flags"]) > 0
        assert "必須条項" in s["spec_with_clauses"]
        assert all(not c["missing"] for c in s["clause_list"])   # bundled library grounds every clause
        assert s["kb_version"]                                    # clause-library revision projected for audit
        assert all(i["human_validation_required"] for i in s["reviewer_checklist"])

    def test_compliance_delta_keys(self):
        state = {"normalized_requirements": _BAD_REQ, "validated_input": _VALIDATED, "correlation_id": "t"}
        d = ProcurementLawComplianceCheckNode().execute(state)
        assert set(d.keys()) == {"compliance_flags", "status"}


class TestFailClosed:
    def test_hydrate_wrong_shape(self):
        assert InputHydrateNode().execute(
            {"user_input": json.dumps({"foo": 1}), "correlation_id": "x"})["status"] == AgentStatus.ERROR.value

    def test_compliance_missing_input(self):
        assert ProcurementLawComplianceCheckNode().execute({"correlation_id": "x"})["status"] == AgentStatus.ERROR.value

    def test_compliance_malformed_rule_config_fails_closed(self):
        # a non-int common_error_rules threshold must yield the fail-closed error result,
        # not an uncaught TypeError from the comparison
        for bad in ({"min_requirement_len": "bad"}, {"min_requirements": None}):
            node = ProcurementLawComplianceCheckNode({"common_error_rules": bad})
            d = node.execute({"normalized_requirements": {"purpose": "調達"}, "correlation_id": "x"})
            assert d["status"] == AgentStatus.ERROR.value

    def test_clause_missing_spec(self):
        assert ComplianceClauseInjectNode().execute({"correlation_id": "x"})["status"] == AgentStatus.ERROR.value

    def test_checklist_missing_input(self):
        assert ReviewChecklistGenerateNode().execute({"correlation_id": "x"})["status"] == AgentStatus.ERROR.value


class TestAuditEvents:
    def test_compliance_emits_domain_event(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cc_mod, "emit_trace_event", lambda et, p, s=None: seen.append(et))
        ProcurementLawComplianceCheckNode().execute(
            {"normalized_requirements": _BAD_REQ, "validated_input": _VALIDATED, "correlation_id": "t"})
        assert "compliance_checked" in seen
