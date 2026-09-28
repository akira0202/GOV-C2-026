"""AgentCore Platform v1.0 — InputValidateNode for GOV-C2-026 PublicProcurementSpecificationDraftAgent."""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state — error_log /
#    node_history carry operator.add reducers, so spreading state double-appends)
#  - Return AgentStatus.<X>.value for status — never the bare enum object
#  - Never import from mediator/, api/, or other agents
#
# Responsibility (docs/02_design.md — outer pre_process / InputValidateNode row):
#  - This is the OUTER pre_process node. It receives the framework-inherited
#    user_input: str (a JSON procurement request), parses and validates it, resolves
#    the config precedence, and writes the canonical, msgpack-safe validated_input.
#    It does NOT build any inner domain value (normalized_requirements, spec_draft,
#    clauses, checklist, audit_metadata) and does NOT touch result — those belong to
#    the inner DomainWorkflowGraph (requirements_normalize onward). The main
#    GraphNode.extract_input() JSON-encodes validated_input into the inner user_input
#    string; the inner input_hydrate restores it (no duplicated S-2 there).
#  - S-2 lives in _extra_security_gate_input(): STRUCTURAL sanitization of the raw
#    string — non-str/binary/NUL/control-char and oversized payloads are rejected
#    there (the raw-string control-char screen also rejects injected control
#    sequences). The framework @final _security_gate_input() (baseline PII scan) is
#    NOT overridden and still runs — that scan, plus the structured-only extraction
#    below (only the contract fields reach validated_input), is the raw-PII posture.
#    Active per-value PII pattern-matching is deliberately avoided so that legitimate
#    vendor / person names in the NL requirement text are not false-positive-blocked.
#    SEMANTIC prompt-injection defense belongs at the inner LLM boundary
#    (requirements_normalize / specification_draft), not this structural gate.
#  - execute() performs business validation only: require caller-input
#    procurement_objective + budget; resolve procurement_type / law_jurisdiction
#    (per-call value first, else the agent.yaml default) and reject only if
#    procurement_type stays unresolved; enforce advisory_only as a code constant
#    (a config false is rejected at construction with ConfigError; a caller-payload
#    false never disables it); assemble the canonical validated_input.

import json
import math
from typing import Any, ClassVar

from framework.errors import ConfigError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.progress import emit_progress
from src.services.degradation import is_caller_correctable
from src.services.failure_message import explain

# Mandatory caller-input fields (docs/02 input contract).
_REQUIRED_CALLER_FIELDS = ("procurement_objective", "budget")
# Optional payload fields carried through to the canonical validated_input.
_OPTIONAL_PAYLOAD_FIELDS = ("timeline", "technical_requirements")
_TIMELINE_MAX = 256
_TECH_REQ_MAX = 20_000
_TECH_REQ_ITEMS_MAX = 500

# Defensive limits (overridable via node_config).
_DEFAULT_MAX_INPUT_CHARS = 1_000_000
_KEY_MAX_LEN = 64  # procurement_type / law_jurisdiction are single-line keys


def _screen_non_text(text: str) -> str | None:
    """S-2 structural sanitization scan — flag non-text / binary content.

    Returns None when acceptable text, else a violation string. NUL is an
    unambiguous binary signal; a high proportion of other control characters
    (excluding tab / newline / carriage-return, legitimate in JSON) indicates
    binary or injected control sequences. No lexical prompt-injection heuristic is
    applied here — semantic prompt-injection is the inner LLM boundary's job.
    """
    if "\x00" in text:
        return "non-text/binary content detected (NUL byte)"
    control = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r")
    if control and control > max(8, len(text) // 100):
        return "non-text/binary content detected (excessive control characters)"
    return None


def _screen_key(value: Any, field: str) -> str | None:
    """Reject injection in a single-line key value (procurement_type,
    law_jurisdiction): non-str, empty, over-length, or any control character
    (NUL / newline / tab / other ord < 32). Does NOT restrict the character set —
    semantic validity (a known type / jurisdiction) is the inner workflow's job."""
    if not isinstance(value, str):
        return f"{field} must be text (str), got {type(value).__name__}"
    if not value:
        return f"{field} is empty"
    if len(value) > _KEY_MAX_LEN:
        return f"{field} exceeds {_KEY_MAX_LEN} characters"
    if any(ord(c) < 32 for c in value):
        return f"{field} contains control characters (possible injection)"
    return None


def _config_str_default(value: Any, field: str) -> str:
    """Validate a config-supplied default for a single-line key (procurement_type,
    law_jurisdiction): it must be a string (or absent/None → ''), is NEVER coerced, and
    must pass the same control-char / length screen as caller values. A non-string or
    malformed config value raises ConfigError at construction (deploy-time fail-fast) —
    an operator's misconfigured list/dict/number is not smuggled through as arbitrary text."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"InputValidateNode: config {field} must be a string (or absent), got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        return ""
    violation = _screen_key(stripped, field)
    if violation:
        raise ConfigError(f"InputValidateNode: config {field} is invalid — {violation}")
    return stripped


def _coerce_budget(value: Any) -> float | None:
    """Return a finite, positive budget number, or None when invalid (rejecting
    bool, NaN/inf, non-positive, junk strings)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(num) or num <= 0:
        return None
    return num


def _nl_injection(value: str, field: str) -> str | None:
    """Structural injection screen for a multi-line NL field (procurement_objective,
    technical_requirements): reject control characters other than tab / newline / CR.
    NUL / binary in the whole payload is already rejected by the raw S-2 scan; multi-line
    NL is allowed; SEMANTIC prompt-injection is neutralized at the inner LLM boundary.
    Satisfies docs/02 'reject injection in NL requirement text' at the structural level."""
    if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        return f"{field} contains control characters (possible injection)"
    return None


def _validate_optional(key: str, value: Any) -> str | None:
    """Enforce the documented typed contract for each optional payload field (not merely
    JSON-safety): `timeline` is a bounded, control-char-free single-line string;
    `technical_requirements` is bounded NL text — a string or a list of strings (multi-line
    NL is allowed; a NUL/binary body is already rejected by the raw S-2 scan). Returns a
    violation string or None. Semantic prompt-injection in the NL text is neutralized at
    the inner LLM boundary (requirements_normalize), not screened here."""
    if key == "timeline":
        if not isinstance(value, str):
            return f"timeline must be a string (got {type(value).__name__})"
        if len(value) > _TIMELINE_MAX:
            return f"timeline exceeds {_TIMELINE_MAX} characters"
        if any(ord(c) < 32 for c in value):
            return "timeline contains control characters (possible injection)"
        return None
    if key == "technical_requirements":
        if isinstance(value, str):
            if len(value) > _TECH_REQ_MAX:
                return "technical_requirements exceeds character limit"
            return _nl_injection(value, "technical_requirements")
        if isinstance(value, list):
            if len(value) > _TECH_REQ_ITEMS_MAX:
                return f"technical_requirements exceeds {_TECH_REQ_ITEMS_MAX} items"
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    return f"technical_requirements[{i}] must be a string (got {type(item).__name__})"
                if len(item) > _TECH_REQ_MAX:
                    return f"technical_requirements[{i}] exceeds character limit"
                inj = _nl_injection(item, f"technical_requirements[{i}]")
                if inj:
                    return inj
            return None
        return f"technical_requirements must be a string or a list of strings (got {type(value).__name__})"
    return None


class InputValidateNode(FunctionNode):
    """Outer pre_process gate — parse and validate the JSON procurement request.

    S-2 (non-str / binary / control-char / oversized) runs in
    _extra_security_gate_input() before execute(); execute() then parses the JSON,
    enforces the caller-input contract + config precedence + the advisory_only code
    constant, and writes the canonical validated_input dict. Read-only capability
    → S-1 VERIFIED_EXTERNAL (procurement objective / budget is business-confidential).
    """

    # S-1: procurement objective / budget is business-confidential → VERIFIED_EXTERNAL
    # (verified caller; matches the manifest). Declared explicitly, not inherited.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        # Config injected by the graph's register_nodes() (agent.yaml → node_config).
        # Defaults keep the node no-arg constructible (the PB-6 invoke-order test
        # instantiates every src/nodes/ class with no args).
        cfg = node_config or {}
        # advisory_only is a CODE-SIDE CONSTANT (true). A config attempting to set it
        # false is rejected at load time — the disclaimer/advisory posture is not a
        # config toggle (docs/02: enforced at input_validate + S-3).
        if "advisory_only" in cfg and cfg["advisory_only"] is not True:
            raise ConfigError(
                "InputValidateNode: advisory_only is a code-side constant (true) and " "MUST NOT be set false in config"
            )
        self._procurement_type_default: str = _config_str_default(cfg.get("procurement_type"), "procurement_type")
        self._law_jurisdiction_default: str = _config_str_default(cfg.get("law_jurisdiction"), "law_jurisdiction")
        self._max_input_chars: int = int(cfg.get("max_input_chars", _DEFAULT_MAX_INPUT_CHARS))

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 domain gate — reject non-str / binary raw input, answer an oversized one.

        Contract: return ``state`` when clean; return a delta dict on violation. Never raise,
        never return None. Runs before execute(); the framework @final _security_gate_input()
        (baseline PII scan) is not overridden.

        Non-text input is refused here and ``execute()`` never runs — that is what this screen is
        for. The size ceiling is answered instead: it is a limit the caller can work within. The
        answered delta carries ``diagnostic_code`` and no ``user_input``, and ``execute()`` returns
        on seeing it, so an oversized body is still never parsed.
        """
        payload = state.get("user_input")
        if payload is None:
            return state  # missing input — structural error surfaced by execute()
        corr = state.get("correlation_id")
        if not isinstance(payload, str):
            # Non-text input — NEVER str()-coerced (that would smuggle binary through).
            return self._reject(
                corr,
                "s2_non_text_type",
                f"user_input must be text (str), got {type(payload).__name__} (S-2)",
                state,
                {"type": type(payload).__name__},
            )
        violation = _screen_non_text(payload)
        if violation:
            return self._reject(corr, "s2_non_text", f"{violation} (S-2)", state, {"detail": violation})
        if len(payload) > self._max_input_chars:
            return self._reject(
                corr,
                "s2_oversized",
                f"input exceeds max_input_chars ({self._max_input_chars}) (S-2)",
                state,
                {"limit": self._max_input_chars},
            )
        return state

    def _reject(
        self,
        corr: Any,
        reason: str,
        message: str,
        state: dict[str, Any],
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Report a rejection to the caller — as an answered notice when the caller can correct it,
        else fail closed.

        Both branches send the same sentence; they differ in what the run reports. A request the
        caller can change completes carrying the code (no draft is built), so the reason reaches
        them as the result of the turn rather than as a terminal failure naming an exception type.
        A refusal this agent owns keeps status: error and its operator-facing error_log.
        """
        emit_progress(explain(reason))
        payload = {"correlation_id": corr, "reason": reason, **(detail or {})}
        if is_caller_correctable(reason):
            emit_trace_event("input_degraded", payload, state)
            return {"status": AgentStatus.SUCCESS.value, "diagnostic_code": reason}
        emit_trace_event("input_validate_rejected", payload, state)
        return {"status": AgentStatus.ERROR.value, "error_log": [f"InputValidateNode: {message}"]}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        # The S-2 gate answers an oversized request rather than refusing it, and hands the answer
        # down as a delta carrying the code and no user_input. Return it untouched: the body was
        # not parsed there and must not be parsed here.
        answered = state.get("diagnostic_code")
        if answered:
            return {"status": AgentStatus.SUCCESS.value, "diagnostic_code": answered}

        emit_progress("Checking the request...")
        raw = state.get("user_input")

        # --- empty / non-str (S-2 already rejects non-str before execute) --------
        if not isinstance(raw, str) or not raw.strip():
            return self._reject(corr, "empty_or_missing", "user_input is empty or not a text document", state)

        # --- parse JSON object ----------------------------------------------------
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return self._reject(corr, "malformed", f"malformed JSON: {exc.__class__.__name__}", state)
        except RecursionError:
            return self._reject(corr, "too_deep", "JSON nesting is too deep", state)
        if not isinstance(payload, dict):
            return self._reject(corr, "not_object", "user_input must be a JSON object", state)

        # --- mandatory caller-input fields ---------------------------------------
        objective = payload.get("procurement_objective")
        if not isinstance(objective, str) or not objective.strip():
            return self._reject(corr, "missing_objective", "procurement_objective is required (non-empty text)", state)
        if len(objective) > _TECH_REQ_MAX:
            return self._reject(
                corr, "objective_too_long", f"procurement_objective exceeds {_TECH_REQ_MAX} characters", state
            )
        inj = _nl_injection(objective, "procurement_objective")
        if inj:
            return self._reject(corr, "objective_invalid", inj, state)
        budget = _coerce_budget(payload.get("budget"))
        if budget is None:
            return self._reject(corr, "missing_budget", "budget is required (a positive number)", state)

        # --- config precedence: per-call value first, else agent.yaml default ----
        # Untrusted per-call values are type-checked strictly, never str()-coerced.
        pt_raw = payload.get("procurement_type")
        if pt_raw is not None and not isinstance(pt_raw, str):
            return self._reject(
                corr, "procurement_type_type", "procurement_type must be text (str) when supplied", state
            )
        procurement_type = (pt_raw or "").strip() or self._procurement_type_default
        if not procurement_type:
            return self._reject(
                corr,
                "procurement_type_unresolved",
                "procurement_type is unresolved (no per-call value and no config default)",
                state,
            )
        violation = _screen_key(procurement_type, "procurement_type")
        if violation:
            return self._reject(corr, "procurement_type_invalid", violation, state)

        lj_raw = payload.get("law_jurisdiction")
        if lj_raw is not None and not isinstance(lj_raw, str):
            return self._reject(
                corr, "law_jurisdiction_type", "law_jurisdiction must be text (str) when supplied", state
            )
        law_jurisdiction = (lj_raw or "").strip() or self._law_jurisdiction_default
        if law_jurisdiction:
            violation = _screen_key(law_jurisdiction, "law_jurisdiction")
            if violation:
                return self._reject(corr, "law_jurisdiction_invalid", violation, state)

        # --- advisory_only: code constant; a caller-payload false never disables it
        if payload.get("advisory_only") is False:
            emit_trace_event(
                "advisory_only_tamper_ignored",
                {"correlation_id": corr, "note": "caller payload advisory_only:false ignored (code constant)"},
                state,
            )

        # --- assemble canonical, msgpack-safe validated_input (structured-only) ---
        validated: dict[str, Any] = {
            "procurement_objective": objective.strip(),
            "budget": budget,
            "procurement_type": procurement_type,
            "advisory_only": True,  # code constant — never caller-overridable
        }
        if law_jurisdiction:
            validated["law_jurisdiction"] = law_jurisdiction
        for key in _OPTIONAL_PAYLOAD_FIELDS:
            if key in payload and payload[key] is not None:
                violation = _validate_optional(key, payload[key])
                if violation:
                    return self._reject(corr, "optional_contract", violation, state)
                validated[key] = payload[key]

        emit_trace_event(
            "input_validated",
            {
                "correlation_id": corr,
                "procurement_type": procurement_type,
                "law_jurisdiction": law_jurisdiction or None,
                "optional_keys": [k for k in _OPTIONAL_PAYLOAD_FIELDS if k in validated],
            },
            state,
        )
        return {"validated_input": validated, "status": AgentStatus.SUCCESS.value}
