"""AgentCore Platform v1.0 — deterministic reviewer-checklist generation for GOV-C2-026.

Pure functions (no framework imports, unit-testable standalone; no LLM). Assembles a
reviewer checklist from the compliance flags plus standard legal-review items. **Every item
is marked `human_validation_required: True`** — the draft is AI-generated and must be
validated by a human before use (国家賠償法 liability boundary). This is decision-support;
it never approves the specification.
"""

from __future__ import annotations

from typing import Any


# Standard legal-review items always presented to the human reviewer.
_STANDARD_ITEMS = [
    ("mandatory_clauses_present", "legal", "必須条項（入札公告 / 前払金 / 検収条件 / APPI）の記載を確認"),
    ("acceptance_criteria_clear", "compliance", "検収・評価基準が具体的かつ測定可能か確認"),
    ("fairness_no_vendor_lock", "fairness", "特定事業者に有利な仕様（vendor lock-in）でないか確認"),
    ("legal_review_required", "legal", "法務（会計法 / 地方自治法）による最終確認"),
]


def generate(spec_with_clauses: str, compliance_flags: list[Any]) -> list[Any]:
    """Return ``reviewer_checklist``: ``[{item, category, human_validation_required}]`` — one
    item per compliance flag plus the standard legal-review items, all human-validation-required."""
    checklist: list[dict[str, Any]] = []
    for f in compliance_flags or []:
        checklist.append(
            {
                "item": f"[{f.get('severity')}] {f.get('message')} (field: {f.get('field')})",
                "category": "compliance_gap",
                "human_validation_required": True,
            }
        )
    for _id, category, text in _STANDARD_ITEMS:
        checklist.append({"item": text, "category": category, "human_validation_required": True})
    return checklist
