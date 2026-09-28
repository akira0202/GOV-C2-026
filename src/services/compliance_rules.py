"""AgentCore Platform v1.0 — deterministic procurement-law compliance rule engine for GOV-C2-026.

Pure functions (no framework imports, unit-testable standalone; no LLM). Checks the
normalized requirement schema against 会計法 / 地方自治法 conventions for the active
``law_jurisdiction`` and flags common specification defects: missing purpose/scope, vague or
missing technical requirements, absent acceptance (evaluation) criteria, missing delivery
timeline / budget ceiling. Thresholds/severities are overridable via ``common_error_rules``;
the code baseline always applies. Output is decision-support (defect flags), never approval.
"""

from __future__ import annotations

from typing import Any

from src.services import jurisdictions
from src.services.domain_errors import DomainComputeError

_DEFAULTS = {
    "min_requirement_len": 10,  # a technical requirement shorter than this is "vague"
    "min_requirements": 1,  # at least this many technical requirements
}


def _empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def check(
    normalized_requirements: Any, *, law_jurisdiction: str = "", rules: dict[str, Any] | None = None
) -> list[Any]:
    """Return ``compliance_flags``: ``[{rule, severity, field, message}]``. Raises
    ``DomainComputeError`` when the requirement schema is not an object."""
    if not isinstance(normalized_requirements, dict):
        raise DomainComputeError("normalized_requirements must be an object")
    cfg = {**_DEFAULTS, **(rules or {})}
    # Fail closed on a malformed rule threshold (never coerced): the values are compared against
    # requirement lengths / counts below, so a non-int (e.g. "bad" / None) would raise an uncaught
    # TypeError instead of the node's fail-closed error result. bool is rejected (not a count).
    for key in ("min_requirement_len", "min_requirements"):
        v = cfg[key]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise DomainComputeError(f"common_error_rules.{key} must be a positive integer")
    nr = normalized_requirements
    flags: list[dict[str, Any]] = []

    def flag(rule: str, severity: str, field: str, message: str) -> None:
        flags.append({"rule": rule, "severity": severity, "field": field, "message": message})

    if _empty(nr.get("purpose")):
        flag("missing_purpose", "High", "purpose", "調達目的（purpose）が未記載です。")
    if _empty(nr.get("scope")):
        flag("missing_scope", "Medium", "scope", "調達範囲（scope）が未記載です。")

    tr = nr.get("technical_requirements")
    if _empty(tr):
        flag(
            "missing_technical_requirements",
            "High",
            "technical_requirements",
            "技術要件（technical_requirements）が未記載です。",
        )
    elif isinstance(tr, list):
        if len(tr) < cfg["min_requirements"]:
            flag(
                "insufficient_requirements",
                "Medium",
                "technical_requirements",
                f"技術要件が {cfg['min_requirements']} 件未満です。",
            )
        for i, item in enumerate(tr):
            if isinstance(item, str) and len(item.strip()) < cfg["min_requirement_len"]:
                flag(
                    "vague_requirement",
                    "Low",
                    f"technical_requirements[{i}]",
                    "技術要件が曖昧・具体性不足です（検収基準を明確化してください）。",
                )

    if _empty(nr.get("evaluation_criteria")):
        flag(
            "absent_acceptance_criteria",
            "High",
            "evaluation_criteria",
            "評価・検収基準（acceptance criteria）が未記載です（会計法 / 地方自治法）。",
        )
    if _empty(nr.get("timeline")):
        flag("missing_delivery_timeline", "High", "timeline", "納期（delivery timeline）が未記載です。")
    if _empty(nr.get("budget_ceiling")):
        flag("missing_budget_ceiling", "Medium", "budget_ceiling", "予算上限（budget ceiling）が未記載です。")

    # A non-empty but unrecognized jurisdiction is surfaced, never silently swallowed: the
    # jurisdiction-specific checks below cannot be applied, so coverage is incomplete and a human
    # must confirm. Empty is the zero-config case (base rules only) and is not flagged.
    lj = str(law_jurisdiction or "")
    if lj.strip() and not jurisdictions.is_recognized(lj):
        flag(
            "unrecognized_jurisdiction",
            "High",
            "law_jurisdiction",
            f"law_jurisdiction「{lj}」は既知の法域（{' / '.join(jurisdictions.KNOWN_JURISDICTIONS)}）"
            "に該当しません。法域固有チェックは未適用です。人間の確認が必要です。",
        )

    # jurisdiction-specific: municipal procurement (地方自治法) requires an explicit 検収 basis
    if "地方自治法" in lj and _empty(nr.get("evaluation_criteria")):
        flag(
            "municipal_inspection_basis",
            "Medium",
            "evaluation_criteria",
            "地方自治法上、検収の基準を仕様書に明記する必要があります。",
        )

    return flags
