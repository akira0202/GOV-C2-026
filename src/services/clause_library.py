"""AgentCore Platform v1.0 — compliance-clause library resolver for GOV-C2-026.

Provides the effective compliance-clause library: the bundled base (``clause_library_data``, authored
by the implementer per docs/02 "KB / Data Files") or a deployment override. Returns the clause text
keyed by clause id plus the library ``version`` and ``source`` provenance, so the mandatory clauses
are GROUNDED on committed authoritative text (never a placeholder) and every output is traceable to
the exact library revision (bound into the S-4 audit metadata as ``kb_version``).

A malformed override fails closed (a procurement specification must not be drafted from an ungrounded
clause source).
"""

from __future__ import annotations

from typing import Any

from src.services import clause_library_data
from src.services.domain_errors import DomainComputeError

_UNVERSIONED = "unversioned"


def _coerce_clauses(clauses: Any) -> dict[str, Any]:
    """Validate a clauses mapping → ``{id: {text, source}}``; fail closed on a malformed entry (an
    ungrounded clause source must not silently become an empty/placeholder library)."""
    if not isinstance(clauses, dict) or not clauses:
        raise DomainComputeError("compliance_clause_library: 'clauses' must be a non-empty mapping")
    out: dict[str, Any] = {}
    for cid, entry in clauses.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"].strip():
            raise DomainComputeError(f"compliance_clause_library: clause '{cid}' must carry non-empty text")
        out[str(cid)] = {
            "text": entry["text"].strip(),
            "source": entry.get("source") if isinstance(entry.get("source"), str) else None,
        }
    return out


def load_bundled_library() -> dict[str, Any]:
    """The committed bundled base clause library + its version / source provenance."""
    return {
        "version": clause_library_data.BUNDLED_VERSION,
        "source": clause_library_data.SOURCE_PROVENANCE,
        "clauses": _coerce_clauses(clause_library_data.BUNDLED_CLAUSES),
    }


def resolve_library(config_override: Any) -> dict[str, Any]:
    """Return the effective clause library. A deployment override (a resolved mapping with ``clauses``
    and an optional ``version`` / ``source``) takes precedence; otherwise the bundled base is used.
    Both are validated — a malformed source fails closed."""
    if isinstance(config_override, dict) and config_override:
        version = config_override.get("version")
        return {
            "version": str(version) if isinstance(version, str) and version.strip() else _UNVERSIONED,
            "source": config_override.get("source") if isinstance(config_override.get("source"), str) else None,
            "clauses": _coerce_clauses(config_override.get("clauses")),
        }
    return load_bundled_library()
