"""Unit tests for the GOV generation / output services (framework-free)."""

import json

import pytest

from src.services import (requirements_normalization as RN, specification_drafting as SD,
                          output_gate as OG)
from src.services.domain_errors import DomainComputeError

_GOOD_NORM = {"purpose": "庁舎ネットワーク更改", "scope": "全庁 LAN",
              "technical_requirements": ["10Gbps 対応スイッチ 50 台"],
              "evaluation_criteria": ["設置後の疎通試験合格"], "timeline": "2025-Q3",
              "budget_ceiling": "5,000,000 円"}


class TestRequirementsNormalization:
    def test_messages_isolate_data_from_system(self):
        msgs = RN.build_normalize_messages({"procurement_objective": "庁舎LAN更改", "budget": 5000000})
        assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
        json.loads(msgs[1]["content"])  # untrusted data is JSON in the user message
        assert "庁舎LAN更改" not in msgs[0]["content"]  # not concatenated into the system task

    def test_valid_result_parsed(self):
        out = RN.parse_and_validate_requirements(json.dumps(_GOOD_NORM))
        assert set(out.keys()) == {"purpose", "scope", "technical_requirements",
                                   "evaluation_criteria", "timeline", "budget_ceiling"}
        assert out["technical_requirements"] == ["10Gbps 対応スイッチ 50 台"]

    def test_non_object_rejected(self):
        with pytest.raises(DomainComputeError):
            RN.parse_and_validate_requirements(json.dumps(["not", "an", "object"]))

    def test_malformed_json_rejected(self):
        with pytest.raises(DomainComputeError):
            RN.parse_and_validate_requirements("not json")

    def test_missing_purpose_rejected(self):
        bad = {**_GOOD_NORM, "purpose": ""}
        with pytest.raises(DomainComputeError):
            RN.parse_and_validate_requirements(json.dumps(bad))

    def test_missing_budget_ceiling_rejected(self):
        bad = {k: v for k, v in _GOOD_NORM.items() if k != "budget_ceiling"}
        with pytest.raises(DomainComputeError):
            RN.parse_and_validate_requirements(json.dumps(bad))

    def test_wrong_list_type_rejected(self):
        bad = {**_GOOD_NORM, "technical_requirements": "should be a list"}
        with pytest.raises(DomainComputeError):
            RN.parse_and_validate_requirements(json.dumps(bad))

    def test_list_with_non_string_rejected(self):
        bad = {**_GOOD_NORM, "evaluation_criteria": ["ok", 123]}
        with pytest.raises(DomainComputeError):
            RN.parse_and_validate_requirements(json.dumps(bad))

    def test_control_char_in_field_rejected(self):
        bad = {**_GOOD_NORM, "purpose": "更改\x00計画"}
        with pytest.raises(DomainComputeError):
            RN.parse_and_validate_requirements(json.dumps(bad))

    def test_budget_ceiling_number_ok(self):
        out = RN.parse_and_validate_requirements(json.dumps({**_GOOD_NORM, "budget_ceiling": 5000000}))
        assert out["budget_ceiling"] == 5000000


class TestSpecificationDrafting:
    def test_messages_isolate_data_and_carry_flags(self):
        flags = [{"rule": "missing_purpose", "severity": "High", "field": "purpose", "message": "x"}]
        msgs = SD.build_spec_messages(_GOOD_NORM, flags)
        assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
        user = json.loads(msgs[1]["content"])
        assert user["compliance_flags"] == flags and user["normalized_requirements"] == _GOOD_NORM

    def test_markdown_draft_accepted(self):
        assert SD.parse_and_validate_spec("# 仕様書\n\n本文") == "# 仕様書\n\n本文"

    def test_json_wrapped_draft_accepted(self):
        assert SD.parse_and_validate_spec(json.dumps({"spec_draft": "# 仕様書"})) == "# 仕様書"

    def test_empty_rejected(self):
        with pytest.raises(DomainComputeError):
            SD.parse_and_validate_spec("   ")

    def test_non_string_rejected(self):
        with pytest.raises(DomainComputeError):
            SD.parse_and_validate_spec(None)

    def test_control_char_rejected(self):
        with pytest.raises(DomainComputeError):
            SD.parse_and_validate_spec("# 仕様書\x00隠しコマンド")


class TestOutputGate:
    def test_disclaimer_prepended_to_string(self):
        out = OG.ensure_disclaimer("# 仕様書\n本文")
        assert out.startswith(OG.ADVISORY_DISCLAIMER) and "本文" in out

    def test_disclaimer_idempotent(self):
        once = OG.ensure_disclaimer("# 仕様書")
        assert OG.ensure_disclaimer(once) == once  # not doubled

    def test_disclaimer_field_added_to_dict(self):
        out = OG.ensure_disclaimer({"spec": "x"})
        assert out["disclaimer"] == OG.ADVISORY_DISCLAIMER and out["spec"] == "x"

    def test_other_value_wrapped(self):
        out = OG.ensure_disclaimer(["a", "b"])
        assert out["disclaimer"] == OG.ADVISORY_DISCLAIMER and out["output"] == ["a", "b"]

    def test_has_disclaimer(self):
        assert OG.has_disclaimer(OG.ensure_disclaimer("x")) is True
        assert OG.has_disclaimer("no disclaimer here") is False
