"""Unit tests for the provisional Stage 5 bounded-fake profile — GOV-C2-026.

The provisional Stage 5 job boots this template in CI and performs one first invoke.
`requirements_normalize` and `specification_draft` are unconditional, fail-closed steps, so that
invoke runs against a bounded fake provider rather than a live one. These tests pin what the fake
must prove — every LLM-boundary validator still executes for real — and what it must never do: be
reachable from a normal deployment, or let an unvalidated response through.

The fake is injected through the node constructor here. The CI environment flag is exercised only
in the tests that assert profile selection itself.
"""

import pytest

from framework.schemas.agent_status import AgentStatus
from src.api import startup as startup_mod
from src.nodes.requirements_normalize_node import RequirementsNormalizeNode
from src.nodes.specification_draft_node import SpecificationDraftNode
from src.services.domain_errors import DomainComputeError
from src.services.requirements_normalization import parse_and_validate_requirements
from src.services.stage5_fakes import FakeDraftLLM

_VALIDATED_INPUT = {
    "procurement_objective": (
        "Supply and installation of a cloud-based document management system for a municipal "
        "records office, including data migration and staff training."
    ),
    "budget": 5000000,
    "technical_requirements": "Web-based access with role-based permissions; full-text search.",
    "procurement_type": "IT",
    "law_jurisdiction": "地方自治法",
}

_SCHEMA_KEYS = {
    "purpose",
    "scope",
    "technical_requirements",
    "evaluation_criteria",
    "timeline",
    "budget_ceiling",
}


def _normalized() -> dict:
    """Run the real normalization node with the fake and return its `normalized_requirements`."""
    result = RequirementsNormalizeNode(llm=FakeDraftLLM()).execute(
        {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
    )
    assert result["status"] == AgentStatus.SUCCESS.value
    return result["normalized_requirements"]


class TestNormalizeContractWithTheFake:
    """The fake is exercised through the real node — what matters is that
    `RequirementsNormalizeNode.execute()` behaves the same as it would with a live provider."""

    def test_success_path_returns_the_full_validated_schema(self):
        result = RequirementsNormalizeNode(llm=FakeDraftLLM()).execute(
            {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
        )

        assert result["status"] == AgentStatus.SUCCESS.value
        assert set(result) == {"normalized_requirements", "status"}
        # the schema shape is produced by the real validator, not copied from the fake
        assert set(result["normalized_requirements"]) == _SCHEMA_KEYS

    def test_the_mandatory_fields_carry_the_callers_own_values(self):
        normalized = _normalized()

        assert _VALIDATED_INPUT["procurement_objective"] == normalized["purpose"]
        assert normalized["budget_ceiling"] == _VALIDATED_INPUT["budget"]

    def test_the_node_reaches_the_provider_boundary_with_the_built_messages(self):
        fake = FakeDraftLLM()
        RequirementsNormalizeNode(llm=fake).execute(
            {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
        )

        assert len(fake.calls) == 1
        assert fake.calls[0][0]["role"] == "system"
        assert fake.calls[0][1]["role"] == "user"

    def test_a_raising_provider_yields_error_and_no_requirements(self):
        fake = FakeDraftLLM(raise_on_complete=RuntimeError("provider 503"))
        result = RequirementsNormalizeNode(llm=fake).execute(
            {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
        )

        assert result["status"] == AgentStatus.ERROR.value
        assert "normalized_requirements" not in result

    def test_a_response_missing_a_required_field_still_fails(self):
        """Guards the guard: the validator is doing real work, not accepting anything the fake
        happens to return."""
        with pytest.raises(DomainComputeError):
            parse_and_validate_requirements('{"scope": "no purpose, no budget ceiling"}')


class TestDraftContractWithTheFake:
    def test_success_path_returns_a_validated_draft(self):
        result = SpecificationDraftNode(llm=FakeDraftLLM()).execute(
            {
                "normalized_requirements": _normalized(),
                "compliance_flags": [],
                "correlation_id": "stage5-bounded-fake",
            }
        )

        assert result["status"] == AgentStatus.SUCCESS.value
        assert set(result) == {"spec_draft", "status"}
        assert result["spec_draft"].strip()

    def test_every_compliance_flag_survives_into_the_draft(self):
        """The drafting contract forbids silencing a deterministic compliance flag; the fake must
        not silence one either, or Stage 5 would pass on a draft a live provider could not."""
        flags = [{"code": "GOV-001", "message": "入札公告の掲載期間を確認すること"}]
        result = SpecificationDraftNode(llm=FakeDraftLLM()).execute(
            {
                "normalized_requirements": _normalized(),
                "compliance_flags": flags,
                "correlation_id": "stage5-bounded-fake",
            }
        )

        assert "GOV-001" in result["spec_draft"]

    def test_a_raising_provider_yields_error_and_no_draft(self):
        fake = FakeDraftLLM(raise_on_complete=RuntimeError("provider 503"))
        result = SpecificationDraftNode(llm=fake).execute(
            {
                "normalized_requirements": _normalized(),
                "compliance_flags": [],
                "correlation_id": "stage5-bounded-fake",
            }
        )

        assert result["status"] == AgentStatus.ERROR.value
        assert "spec_draft" not in result


class TestProfileSelection:
    def test_disabled_without_the_ci_flag(self, monkeypatch):
        monkeypatch.delenv("AGENT_STAGE5_BOUNDED_FAKE", raising=False)
        assert startup_mod.bounded_fake_profile_enabled() is False

    def test_enabled_only_by_the_exact_flag_value(self, monkeypatch):
        monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "true")
        assert startup_mod.bounded_fake_profile_enabled() is False
        monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")
        assert startup_mod.bounded_fake_profile_enabled() is True

    def test_profile_supplies_the_fake_through_the_config_channel(self):
        """`llm` is the same config key any caller would use; graph.py already prefers a supplied
        adapter over its default, so the profile needs no bespoke constructor keyword."""
        overlay = startup_mod.bounded_fake_profile()

        assert isinstance(overlay["llm"], FakeDraftLLM)
        assert set(overlay) == {"llm"}

    def test_shipped_config_carries_no_llm_key(self):
        """Nothing in the runtime config can select the fake: a deployment always gets the default
        provider adapter the graph injects."""
        assert "llm" not in startup_mod.load_runtime_config()
