"""Stage 5 failure-classification projection — safe and useful.

Two guarantees:
  (1) `src/services/stg_error.classify` reduces a node error_log to an allowlisted
      "NodeName: ErrorClass" label and never returns a raw message / input / secret; and
  (2) end-to-end, a failed inner run projects that allowlisted `stg_error` into the outer
      HTTP response, so the deploy-stg diagnostic can classify the failure — while a raised
      exception body (message, procurement input fragment, credential-like string) never
      reaches the response.
"""

import json
from pathlib import Path

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import PublicProcurementSpecificationDraftAgentGraph as Graph
from src.services import stg_error

_CANONICAL_INPUT = json.loads(
    (Path(__file__).resolve().parents[2] / "deploy" / "invoke_payload.json").read_text()
)["input"]


class _RaisingLLM:
    """Simulates a provider/secret/transport failure inside an LLM node with a body that
    must NOT reach the response."""

    def complete(self, messages, state):
        raise RuntimeError("boom sk-live-ABCDEF0123456789 procurement 'Supply 50 PCs' 999")


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


class TestClassifyUnit:
    def test_bare_class_kept_and_bodies_collapsed(self):
        assert stg_error.classify(["RequirementsNormalizeNode: ConnectionError"]) == \
            "RequirementsNormalizeNode: ConnectionError"
        out = stg_error.classify(["SpecificationDraftNode: sk-live-abc Bearer eyJ and 12345"])
        assert out == "SpecificationDraftNode: unknown_error"
        assert "sk-live" not in out and "Bearer" not in out and "12345" not in out

    def test_empty_or_non_list_is_unknown(self):
        assert stg_error.classify([]) == "unknown_node: unknown_error"
        assert stg_error.classify(None) == "unknown_node: unknown_error"
        assert stg_error.classify("RequirementsNormalizeNode: ValueError") == \
            "RequirementsNormalizeNode: ValueError"


class TestFailureProjection:
    def test_failure_projects_allowlisted_stg_error_without_body(self):
        result = Graph(config={"llm": _RaisingLLM()}).invoke(_CANONICAL_INPUT, ctx=_ctx())
        assert result.get("status") == AgentStatus.ERROR.value
        err = result.get("stg_error")
        assert isinstance(err, str) and err, "failure classification must reach the response"
        # allowlisted "NodeName: ErrorClass" (or unknown_error) — never the raised body
        for leaked in ("sk-live", "boom", "Supply", "999", "procurement '"):
            assert leaked not in err
        node = err.split(": ", 1)[0]
        assert node.replace("_", "").isalnum(), f"node prefix must be identifier-shaped: {err!r}"
