"""AgentCore Platform v1.0 — audit-metadata assembly for GOV-C2-026.

Pure functions (no framework imports; unit-testable standalone). Assembles the S-4 audit
artifact for the FSA / procurement-audit trail. Raw procurement bodies are NEVER persisted —
only a content hash. The inner ``get_output()`` returns ``{output, audit_metadata}``; this
module builds the ``audit_metadata`` and the final ``output`` payload from the deterministic
draft + clauses + reviewer checklist.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _hash(*parts: Any) -> str:
    """Stable content hash of the assembled bodies (hashes only — no raw body persisted)."""
    blob = json.dumps(parts, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_audit_metadata(
    *,
    spec_with_clauses: Any,
    clause_list: Any,
    reviewer_checklist: Any,
    law_jurisdiction: Any,
    kb_version: Any,
    model_version: Any,
    generated_at: str,
) -> dict[str, Any]:
    """Return the ``audit_metadata`` artifact: ``{data_hash, generated_at, kb_version,
    model_version, law_jurisdiction}`` — hashes only, no raw procurement body."""
    return {
        "data_hash": _hash(spec_with_clauses, clause_list, reviewer_checklist),
        "generated_at": generated_at,
        "kb_version": kb_version,
        "model_version": model_version,
        "law_jurisdiction": law_jurisdiction,
    }


def build_output(*, spec_with_clauses: Any, clause_list: Any, reviewer_checklist: Any) -> dict[str, Any]:
    """Assemble the final human-review payload projected to the outer ``result``: the specification
    (draft + mandatory clauses), the clause list, and the reviewer checklist."""
    return {
        "specification": spec_with_clauses,
        "clause_list": clause_list if isinstance(clause_list, list) else [],
        "reviewer_checklist": reviewer_checklist if isinstance(reviewer_checklist, list) else [],
    }
