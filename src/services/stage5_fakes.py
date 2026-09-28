"""AgentCore Platform v1.0 — bounded fake for the provisional Stage 5 CI profile.

NOT a deployment path. Selected only by the explicit CI profile resolved in `src/api/startup.py`;
the shipped runtime config cannot reach it, and no production or AgentRegistry path constructs it.

Why it exists: the provisional Stage 5 job boots the template inside a CI container and performs
one first invoke. `requirements_normalize` and `specification_draft` are unconditional, fail-closed
steps, so a live run would have to call a real LLM provider — which means a provider credential in
CI, a per-run cost, and a non-deterministic response that can fail the evidence assertion for
reasons unrelated to this template. The bounded fake keeps the whole workflow — hydration, schema
validation, the deterministic compliance check, mandatory clause injection, the reviewer checklist,
the S-3 output gate and the audit trail — executing exactly as in production, and replaces only the
provider call. What Stage 5 then proves is the deploy -> invoke -> workflow contract, NOT that a
live provider call succeeds. See docs/07 for that distinction.

The responses below are derived from the request the node actually sent, so the real validators run
against them for real: the normalized object carries every schema key `parse_and_validate_requirements`
requires (including the mandatory `purpose` and `budget_ceiling`), and the draft is checked by
`parse_and_validate_spec` like any other draft. Nothing here bypasses `parse_and_validate_*`.
"""

import json
from typing import Any

_DRAFT_NOTE = "本文書は暫定 Stage 5 プロファイルが生成した合成ドラフトであり、モデルは呼び出されていません。"


def _normalized_requirements(payload: dict[str, Any]) -> str:
    objective = payload.get("procurement_objective")
    technical = payload.get("technical_requirements")
    budget = payload.get("budget")
    return json.dumps(
        {
            "purpose": str(objective) if isinstance(objective, str) and objective.strip() else "調達目的（未記載）",
            "scope": "",
            # The caller's technical_requirements arrive as free text; the schema wants a list.
            "technical_requirements": [technical] if isinstance(technical, str) and technical.strip() else [],
            "evaluation_criteria": [],
            "timeline": "",
            # budget_ceiling is a required field: text or a number, never empty.
            "budget_ceiling": budget if isinstance(budget, (int, float)) and not isinstance(budget, bool) else "未設定",
        },
        ensure_ascii=True,
    )


def _spec_draft(payload: dict[str, Any]) -> str:
    requirements = payload.get("normalized_requirements")
    requirements = requirements if isinstance(requirements, dict) else {}
    flags = payload.get("compliance_flags")
    flags = flags if isinstance(flags, list) else []
    lines = [
        "# 仕様書ドラフト（暫定 Stage 5 プロファイル）",
        "",
        _DRAFT_NOTE,
        "",
        "## 調達の目的",
        "",
        str(requirements.get("purpose") or "（未記載）"),
        "",
        "## 審査担当者が確認すべき事項",
        "",
    ]
    if flags:
        # Every compliance flag must survive into the draft — the system prompt forbids silencing one,
        # so the fake must not silence one either.
        lines += [f"- {json.dumps(flag, ensure_ascii=False)}" for flag in flags]
    else:
        lines.append("- 決定論的コンプライアンス判定は指摘事項を返しませんでした。")
    return "\n".join(lines) + "\n"


class FakeDraftLLM:
    """In-process stand-in for `AzureOpenAIDraftLLM` — contacts no provider.

    Implements the same interface the LLM nodes call, `complete(messages, state) -> str`, and
    records every call so a test can assert a node actually reached the provider boundary with the
    messages its own message builder produced.

    Which of the two requests it received is decided from the request payload the node sent, not
    from any out-of-band flag: only the normalization request carries `procurement_objective`, and
    only the drafting request carries `normalized_requirements`.
    """

    def __init__(self, raise_on_complete: Exception | None = None) -> None:
        self._raise_on_complete = raise_on_complete
        self.calls: list[list[Any]] = []

    def complete(self, messages: list[Any], state: dict[str, Any]) -> str:
        self.calls.append(messages)
        if self._raise_on_complete is not None:
            raise self._raise_on_complete
        payload = self._payload(messages)
        if "normalized_requirements" in payload:
            return _spec_draft(payload)
        return _normalized_requirements(payload)

    @staticmethod
    def _payload(messages: list[Any]) -> dict[str, Any]:
        """The untrusted data always travels in the SEPARATE user message the builders emit."""
        for message in reversed(messages if isinstance(messages, list) else []):
            if isinstance(message, dict) and message.get("role") == "user":
                try:
                    data = json.loads(message.get("content", ""))
                except (TypeError, ValueError):
                    return {}
                return data if isinstance(data, dict) else {}
        return {}
