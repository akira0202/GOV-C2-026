"""AgentCore Platform v1.0 — deterministic mandatory-clause injection for GOV-C2-026.

Pure functions (no framework imports, unit-testable standalone; no LLM). Selects the
mandatory clauses required for the active ``law_jurisdiction`` (会計法 central /
地方自治法 municipal) and injects them into the specification draft. The set of REQUIRED
clause types is a code baseline; the clause TEXT is sourced from the injected
``compliance_clause_library``. Every mandatory clause must be grounded on committed library
text — a required clause with no text FAILS CLOSED (never an ungrounded placeholder draft).
"""

from __future__ import annotations

from typing import Any

from src.services.domain_errors import DomainComputeError
from src.services.jurisdictions import KNOWN_JURISDICTIONS

# Required mandatory clause types (id -> human title). Common set applies to every
# jurisdiction; jurisdiction-specific sets are added on top.
_COMMON = {
    "nyusatsu_koukoku": "入札公告",
    "appi_kojin_joho": "APPI / 個人情報保護 取扱条項",
    "kensyu_joken": "検収条件",
}
_BY_JURISDICTION = {
    "会計法": {"maebaraikin_genya_central": "前払金限度額（会計法・国）"},
    "地方自治法": {"maebaraikin_genya_municipal": "前払金限度額（地方自治法・自治体）"},
}
# 議会議決（地方自治法第96条第1項第5号）is required ONLY for a 工事又は製造の請負契約
# (construction / manufacturing procurement) — NOT for goods / services. It is therefore selected by
# procurement_type as well as jurisdiction (a type-scoped mandatory clause).
_MUNICIPAL_CONSTRUCTION = {"gikai_giketsu": "議会議決 要否の確認"}
# Only 工事 (construction) / 製造 (manufacturing) markers — NOT a bare 請負, which also covers
# 役務の請負 (service contracts), for which 議会議決 is not required.
_CONSTRUCTION_MANUFACTURING_MARKERS = ("construction", "build", "工事", "manufactur", "fabricat", "製造")


def _is_construction_or_manufacturing(procurement_type: str) -> bool:
    pt = str(procurement_type or "").lower()
    return any(m in pt for m in _CONSTRUCTION_MANUFACTURING_MARKERS)


def _required_clause_ids(law_jurisdiction: str, procurement_type: str = "") -> dict[str, Any]:
    required = dict(_COMMON)
    lj = str(law_jurisdiction or "")
    matched = False
    for key in KNOWN_JURISDICTIONS:
        if key in lj:
            required.update(_BY_JURISDICTION.get(key, {}))
            matched = True
    # 議会議決 applies only to municipal 工事/製造 請負 (地方自治法第96条) — type-scoped.
    if "地方自治法" in lj and _is_construction_or_manufacturing(procurement_type):
        required.update(_MUNICIPAL_CONSTRUCTION)
    # A non-empty but unrecognized jurisdiction did not select any jurisdiction-specific mandatory
    # clause set. Surface that gap through the unrecognized_jurisdiction review flag rather than
    # silently injecting only the common clauses.
    if lj.strip() and not matched:
        required["unrecognized_jurisdiction"] = "未知の法域（法域固有の必須条項が未選択・要確認）"
    return required


def inject(
    spec_draft: Any,
    *,
    law_jurisdiction: str = "",
    procurement_type: str = "",
    clause_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{"spec_with_clauses": str, "clause_list": [{id, title, source, jurisdiction, missing}]}``.

    Every mandatory clause must be GROUNDED on committed library text. A required clause whose text is
    unavailable in the library **fails closed** (``DomainComputeError``) — a specification with
    required legal text absent must not be returned as a usable human-review draft. The
    ``unrecognized_jurisdiction`` marker is a jurisdiction-selection review flag (not a missing clause
    text) and is surfaced without blocking the base draft."""
    draft = spec_draft if isinstance(spec_draft, str) else ""
    library = clause_library or {}
    required = _required_clause_ids(law_jurisdiction, procurement_type)

    clause_list: list[dict[str, Any]] = []
    rendered: list[str] = []
    missing_required: list[str] = []
    for cid, title in required.items():
        if cid == "unrecognized_jurisdiction":
            # jurisdiction-selection review flag, not a grounded clause with library text
            clause_list.append(
                {"id": cid, "title": title, "source": None, "jurisdiction": str(law_jurisdiction), "missing": True}
            )
            continue
        entry = library.get(cid) if isinstance(library.get(cid), dict) else None
        text = entry.get("text") if entry else None
        if entry is None or not (isinstance(text, str) and text.strip()):
            missing_required.append(cid)
            continue
        clause_list.append(
            {
                "id": cid,
                "title": title,
                "source": entry.get("source"),
                "jurisdiction": str(law_jurisdiction),
                "missing": False,
            }
        )
        rendered.append(f"### {title}\n{text.strip()}")

    if missing_required:
        # fail closed before draft output — never emit an ungrounded specification.
        raise DomainComputeError(
            f"compliance_clause_inject: mandatory clause text unavailable for {sorted(missing_required)} "
            "in the clause library — refusing to draft an ungrounded specification (fail closed)"
        )

    spec_with_clauses = draft.rstrip() + "\n\n## 必須条項（mandatory clauses）\n\n" + "\n\n".join(rendered)
    return {"spec_with_clauses": spec_with_clauses, "clause_list": clause_list}
