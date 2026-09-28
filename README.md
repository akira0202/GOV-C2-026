# GOV-C2-026 — PublicProcurementSpecificationDraftAgent

> **Category**: Cat 2 (multi-step domain workflow)
> **Industry**: GOV

## Overview

This agent drafts a public-procurement specification (仕様書) from a plain-language
description of what is being procured. You give it the objective, budget, timeline, and technical
requirements; it produces a draft following the format conventions used by Japanese public bodies,
inserts the clauses that are mandatory under the Public Accounts Act (会計法) and the Local
Autonomy Act (地方自治法), and generates a checklist for the reviewer to work through.

The draft is always advisory. It is not a legal instrument, it does not approve or issue a
procurement, and the procurement officer keeps sole legal responsibility for what is finally
published.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## What it returns

- **A specification draft** when the request is accepted: the drafted sections with the mandatory
  compliance clauses inserted and a reviewer checklist, always carrying the advisory disclaimer. It
  is a draft for human review, never a final legal instrument.
- **A short notice** when the request is one you can correct — more content than the limit allows,
  a body that is not a JSON object, a missing or unreadable procurement objective or budget, a
  field that is too long or holds characters that are not allowed, a procurement type or law
  jurisdiction that cannot be resolved, or an optional field that does not match its documented
  type. The run completes and the answer names what to change. No draft is built, and an empty
  specification is never returned in its place.
- **An error with no output** when the input is not a text document at all (binary content, a NUL
  byte, a flood of control characters), when the advisory posture is not intact at the output gate,
  or when a step inside the workflow cannot complete — including a required clause whose source
  text is missing, since a specification with legal text absent must not be handed over as usable.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/` for the design specification and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

