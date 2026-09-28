"""Stage 5 provisional-deploy regression — distinguishes template bugs from the live-LLM gap.

The provisional deploy-stg boots src/api/server.py, which loads config/config.yaml and passes
it as Graph(config=...); that config declares no `llm`, so register_nodes() falls back to the
keyless AzureOpenAIDraftLLM. GOV's requirements-normalize and specification-draft LLM nodes are
mandatory and fail-closed (`error_strategy=propagate`),
so the first invoke makes a live LLM call. If `AZURE_OPENAI_*` is not resolvable in the CI env (or
egress fails), the invoke returns `status: error` — an infrastructure gap, not a template defect.

This test proves the template side is correct: with a WORKING (fake) LLM injected, the canonical
`deploy/invoke_payload.json` runs the full procurement-drafting workflow to `status: success`.
So a deploy-stg FAIL on the same payload is the live-LLM path (key/egress), not the payload,
wiring, parser, or workflow contract. Synthetic input only; no live model call; no real
procurement data.
"""

import json
from pathlib import Path

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import PublicProcurementSpecificationDraftAgentGraph as Graph

_CANONICAL_INPUT = json.loads((Path(__file__).resolve().parents[2] / "deploy" / "invoke_payload.json").read_text())[
    "input"
]


class _FakeLLM:
    """One adapter serves both LLM nodes; branches on the system task (mirrors test_pb_workflow)."""

    def complete(self, messages, state):
        system = messages[0]["content"]
        if "structured schema" in system:  # requirements_normalize
            return json.dumps(
                {
                    "purpose": "庁舎ネットワーク更改",
                    "scope": "全庁 LAN",
                    "technical_requirements": ["10Gbps 対応スイッチ"],
                    "evaluation_criteria": ["疎通試験合格"],
                    "timeline": "2025-Q3",
                    "budget_ceiling": "5,000,000 円",
                }
            )
        return "# 調達仕様書\n\n## 調達目的\n庁舎ネットワーク更改"  # specification_draft


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


class TestStgCanonicalPayload:
    def test_canonical_payload_succeeds_with_working_llm(self):
        # config-less graph except a working (fake) LLM — the template side of the deploy-stg path.
        # procurement_type / law_jurisdiction are supplied by the payload (config-less has no defaults).
        result = Graph(config={"llm": _FakeLLM()}).invoke(_CANONICAL_INPUT, ctx=_ctx())
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            "canonical STG payload must reach status:success with a working LLM "
            f"(so a deploy-stg FAIL is the live-LLM path, not the template): "
            f"{result.get('status')} / {result.get('error_log')}"
        )
        assert result.get("output")
