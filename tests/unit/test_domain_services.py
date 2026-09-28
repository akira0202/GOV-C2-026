"""Unit tests for the GOV deterministic domain services (framework-free)."""

import pytest

from src.services import (compliance_rules as CR, clause_injection as CI, review_checklist as RC,
                          clause_library)
from src.services.domain_errors import DomainComputeError

_LIB = clause_library.load_bundled_library()["clauses"]  # the committed bundled base clause text

_GOOD = {"purpose": "庁舎ネットワーク更改", "scope": "全庁 LAN 機器の調達と設置",
         "technical_requirements": ["10Gbps 対応スイッチを50台導入し既存VLAN構成を維持"],
         "evaluation_criteria": ["設置後の疎通試験合格"], "timeline": "2025-Q3", "budget_ceiling": 5000000}
_BAD = {"purpose": "", "technical_requirements": [], "evaluation_criteria": [], "timeline": ""}


class TestComplianceRules:
    def test_complete_requirements_no_high_flags(self):
        assert all(f["severity"] != "High" for f in CR.check(_GOOD, law_jurisdiction="地方自治法"))

    def test_missing_fields_flagged(self):
        rules = {f["rule"] for f in CR.check(_BAD, law_jurisdiction="会計法")}
        assert {"missing_purpose", "absent_acceptance_criteria", "missing_delivery_timeline"} <= rules

    def test_municipal_jurisdiction_inspection_basis(self):
        rules = {f["rule"] for f in CR.check(_BAD, law_jurisdiction="地方自治法")}
        assert "municipal_inspection_basis" in rules

    def test_vague_requirement_flagged(self):
        nr = {**_GOOD, "technical_requirements": ["速い"]}  # too short → vague
        assert any(f["rule"] == "vague_requirement" for f in CR.check(nr))

    def test_non_dict_rejected(self):
        with pytest.raises(DomainComputeError):
            CR.check("not a dict")

    def test_malformed_rule_config_rejected(self):
        # a non-int / non-positive rule threshold must fail closed, not raise an uncaught TypeError
        for bad in ({"min_requirement_len": "bad"}, {"min_requirements": None},
                    {"min_requirement_len": True}, {"min_requirements": 0}, {"min_requirement_len": -1}):
            with pytest.raises(DomainComputeError):
                CR.check(_GOOD, rules=bad)

    def test_known_jurisdictions_not_flagged_unrecognized(self):
        for lj in ("会計法", "地方自治法"):
            assert not any(f["rule"] == "unrecognized_jurisdiction" for f in CR.check(_GOOD, law_jurisdiction=lj))

    def test_empty_jurisdiction_zero_config_not_flagged(self):
        assert not any(f["rule"] == "unrecognized_jurisdiction" for f in CR.check(_GOOD, law_jurisdiction=""))

    def test_unknown_jurisdiction_surfaced_not_swallowed(self):
        flags = CR.check(_GOOD, law_jurisdiction="民法")
        uj = [f for f in flags if f["rule"] == "unrecognized_jurisdiction"]
        assert len(uj) == 1 and uj[0]["severity"] == "High" and uj[0]["field"] == "law_jurisdiction"
        # a garbage jurisdiction did not silently get the municipal-specific rule either
        assert not any(f["rule"] == "municipal_inspection_basis" for f in flags)


class TestClauseInjection:
    def test_common_and_municipal_clauses_selected(self):
        res = CI.inject("# 仕様書", law_jurisdiction="地方自治法", procurement_type="construction", clause_library=_LIB)
        ids = {c["id"] for c in res["clause_list"]}
        assert {"nyusatsu_koukoku", "appi_kojin_joho", "maebaraikin_genya_municipal", "gikai_giketsu"} <= ids

    def test_gikai_giketsu_only_for_construction_or_manufacturing(self):
        # 議会議決 (地方自治法第96条) applies only to 工事又は製造の請負 — type-scoped, not for goods/services
        for pt in ("construction", "工事の請負", "manufacturing", "製造"):
            ids = {c["id"] for c in CI.inject("# s", law_jurisdiction="地方自治法", procurement_type=pt, clause_library=_LIB)["clause_list"]}
            assert "gikai_giketsu" in ids, pt
        for pt in ("IT-services", "物品購入", "役務", ""):
            ids = {c["id"] for c in CI.inject("# s", law_jurisdiction="地方自治法", procurement_type=pt, clause_library=_LIB)["clause_list"]}
            assert "gikai_giketsu" not in ids and "maebaraikin_genya_municipal" in ids, pt

    def test_central_no_gikai_regardless_of_type(self):
        ids = {c["id"] for c in CI.inject("# s", law_jurisdiction="会計法", procurement_type="construction", clause_library=_LIB)["clause_list"]}
        assert "gikai_giketsu" not in ids

    def test_central_jurisdiction_clauses(self):
        res = CI.inject("# 仕様書", law_jurisdiction="会計法", clause_library=_LIB)
        ids = {c["id"] for c in res["clause_list"]}
        assert "maebaraikin_genya_central" in ids and "gikai_giketsu" not in ids

    def test_bundled_library_grounds_every_clause(self):
        res = CI.inject("# 仕様書", law_jurisdiction="会計法", clause_library=_LIB)
        assert all(not c["missing"] for c in res["clause_list"])  # bundled base grounds every clause

    def test_missing_mandatory_clause_fails_closed(self):
        # a mandatory clause with no grounded text must NOT produce a usable draft
        with pytest.raises(DomainComputeError):
            CI.inject("# 仕様書", law_jurisdiction="会計法", clause_library={})

    def test_partial_library_fails_closed(self):
        # only one of the required clauses has text → the rest are ungrounded → fail closed
        lib = {"nyusatsu_koukoku": {"text": "入札公告は…", "source": "総務省"}}
        with pytest.raises(DomainComputeError):
            CI.inject("# 仕様書", law_jurisdiction="会計法", clause_library=lib)

    def test_spec_appended_with_grounded_clause_text(self):
        out = CI.inject("# 仕様書\n本文", law_jurisdiction="会計法", clause_library=_LIB)["spec_with_clauses"]
        assert "必須条項" in out and "入札公告" in out  # grounded authoritative clause text, not a placeholder

    def test_unknown_jurisdiction_surfaced_not_blocking(self):
        # a garbage jurisdiction surfaces the unrecognized_jurisdiction review flag; the common
        # clauses remain grounded so the base draft is still produced (docs/02: not a hard error)
        res = CI.inject("# 仕様書", law_jurisdiction="民法", clause_library=_LIB)
        uj = [c for c in res["clause_list"] if c["id"] == "unrecognized_jurisdiction"]
        assert len(uj) == 1 and uj[0]["missing"] is True
        ids = {c["id"] for c in res["clause_list"]}
        assert "maebaraikin_genya_central" not in ids and "maebaraikin_genya_municipal" not in ids

    def test_empty_jurisdiction_no_unrecognized_entry(self):
        res = CI.inject("# 仕様書", law_jurisdiction="", clause_library=_LIB)
        assert not any(c["id"] == "unrecognized_jurisdiction" for c in res["clause_list"])


class TestReviewChecklist:
    def test_every_item_human_validation_required(self):
        flags = CR.check(_BAD, law_jurisdiction="地方自治法")
        chk = RC.generate("# spec", flags)
        assert chk and all(i["human_validation_required"] is True for i in chk)

    def test_compliance_flags_become_checklist_items(self):
        flags = CR.check(_BAD)
        chk = RC.generate("# spec", flags)
        assert any(i["category"] == "compliance_gap" for i in chk)

    def test_standard_legal_items_always_present(self):
        chk = RC.generate("# spec", [])
        assert any(i["category"] == "legal" for i in chk)
