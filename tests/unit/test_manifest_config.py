"""Manifest / runtime-config test — prove the DEPLOYED configuration actually provides
the documented InputValidate defaults, so future docs↔config drift fails CI (requires
the framework — runs in CI).

Two files, two roles: ``config/agent.yaml`` is the static discovery manifest (identity +
a single dotted class: path), and ``config/config.yaml`` carries the runtime parameters —
AgentRegistry loads the latter separately and passes it as ``Graph(config=...)``, and
``src/api/server.py`` loads it the same way. The graph wiring test proves an in-memory
config resolves; this test loads the real ``config/config.yaml`` (the node_config actually
injected) and proves the default resolves through the graph. Deleting a documented default
from config.yaml fails here.
"""

import importlib
import json
import re
from pathlib import Path

import yaml

from framework.schemas.agent_status import AgentStatus

from src.graph.graph import PublicProcurementSpecificationDraftAgentGraph as Graph

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _ROOT / "config" / "agent.yaml"
_CONFIG = _ROOT / "config" / "config.yaml"


def _manifest() -> dict:
    # Root-level AgentRegistry schema (no `agent:` wrapper).
    return yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))


def _manifest_config() -> dict:
    # Runtime parameters live in config/config.yaml, never in the static manifest.
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def _obj(**extra):
    d = {"procurement_objective": "Supply and install 50 office PCs", "budget": 1_000_000}
    d.update(extra)
    return json.dumps(d)


class TestManifestConfig:
    def test_manifest_declares_input_validate_defaults(self):
        cfg = _manifest_config()
        assert cfg.get("advisory_only") is True
        assert isinstance(cfg.get("procurement_type"), str) and cfg["procurement_type"].strip()
        assert isinstance(cfg.get("law_jurisdiction"), str) and cfg["law_jurisdiction"].strip()

    def test_manifest_declares_importable_class(self):
        m = _manifest()
        assert m["base_type"] == "Other"  # L1-direct
        # Single dotted class path (module.Class) — no legacy module:/class: split.
        assert "module" not in m, "manifest must use a single dotted class:, not module:/class:"
        module_path, class_name = m["class"].rsplit(".", 1)
        mod = importlib.import_module(module_path)
        assert hasattr(mod, class_name), f"manifest class {m['class']} not importable"

    def test_manifest_declares_every_resolved_secret(self):
        secrets = _manifest().get("requires", {}).get("secrets", [])
        for key in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"):
            assert key in secrets, f"{key} is resolved in src/ but not declared in requires.secrets"

    def test_no_unresolved_placeholders_in_runtime_config_docs(self):
        # deploy-time identity must be resolved — a stray {{...}} token fails CI
        token = re.compile(r"\{\{[A-Z0-9_]+\}\}")
        targets = list((_ROOT / "config").rglob("*.yaml")) + list((_ROOT / "src").rglob("*.py")) + [_ROOT / "README.md"]
        offenders = [
            str(p.relative_to(_ROOT))
            for p in targets
            if "__pycache__" not in str(p) and token.search(p.read_text(encoding="utf-8"))
        ]
        assert offenders == [], f"unresolved placeholders in: {offenders}"

    def test_manifest_default_resolves_through_the_graph(self):
        # a zero-per-call invocation must succeed because config.yaml supplies the default
        cfg = _manifest_config()
        g = Graph(config=cfg)
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _obj(), "correlation_id": "m"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["validated_input"]["procurement_type"] == cfg["procurement_type"]

    def test_manifest_advisory_only_not_disabled(self):
        # config.yaml must not ship advisory_only:false (would raise ConfigError at load)
        assert _manifest_config().get("advisory_only") is not False
