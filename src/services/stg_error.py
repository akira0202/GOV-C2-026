"""Allowlisted failure classification for the Stage 5 provisional-deploy diagnostic.

A failed inner run carries a node ``error_log``. The outer graph projects ONLY the
classification produced here into the HTTP response (as ``stg_error``) so the CI
``deploy-stg`` diagnostic can distinguish an LLM key / SecretProvider / provider-egress
gap from a template-side output / parser / workflow defect — without ever exposing a
raw error message, response body, prompt, procurement input, or secret.

``classify`` reduces the first error_log entry to a ``"NodeName: ErrorClass"`` label:
only an identifier-shaped node prefix and a bare exception-class token survive; any
message- or data-bearing tail collapses to ``unknown_error`` and a non-identifier prefix
to ``unknown_node``. The raw entry is never returned.
"""

import re

_NODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _classify_entry(entry: object) -> str:
    text = entry if isinstance(entry, str) else ""
    node, sep, rest = text.partition(": ")
    node = node.strip()
    rest = rest.strip()
    safe_node = node if _NODE_RE.match(node) else "unknown_node"
    safe_err = rest if (sep and _CLASS_RE.match(rest)) else "unknown_error"
    return f"{safe_node}: {safe_err}"


def classify(error_log: object) -> str:
    """Return an allowlisted ``NodeName: ErrorClass`` label for the first error_log entry.

    Never returns any raw message, response, input, or secret. Absent/empty error_log →
    ``unknown_node: unknown_error``.
    """
    if isinstance(error_log, (list, tuple)) and error_log:
        return _classify_entry(error_log[0])
    if isinstance(error_log, str):
        return _classify_entry(error_log)
    return "unknown_node: unknown_error"
