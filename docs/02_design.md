# Template Design Specification — GOV-C2-026 PublicProcurementSpecificationDraftAgent

## Agent Overview

Assists public procurement officers in drafting structured 仕様書 (procurement specifications).
Given a procurement objective, budget, timeline, and technical requirements in natural language, it
normalizes the input into a structured requirement schema, runs a rule-based compliance check against
会計法 / 地方自治法 conventions, drafts a 総務省 / 内閣府-format specification grounded on a
procurement-law KB, injects mandatory compliance clauses from a clause library, and produces a
reviewer checklist that marks AI-generated items for human validation. Every output carries an
advisory disclaimer (`advisory_only: true`) — the output is a **draft for human review**, never
auto-submitted and never a final legal instrument. **The procurement officer retains sole legal
responsibility.**

## Position in AgentCore Architecture

- **Agent Class**: `PublicProcurementSpecificationDraftAgent` (inherits `AgentBaseGraph`)
- **L1 Base**: **AgentBaseGraph** (L1-direct). `DocGenerationAgent` is a **pattern reference only**,
  not an inheritance target.
- **Cat 2 structure**: the outer `AgentBaseGraph` keeps the fixed backbone; the multi-step domain
  workflow is an inner `BaseGraph` (`src/graph/domain_workflow_graph.py`) wrapped by a `GraphNode`
  in the `main` slot. **The outer graph does NOT override `add_edges()`** — the framework wires the
  backbone.
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()`; backbone wiring left to the framework)

## Architecture Overview

Cat 2 two-layer composition. `procurement_law_compliance_check` is a deterministic rule engine;
`requirements_normalize` and `specification_draft` are the LLM steps. **S-1 trust is enforced at
the OUTER boundary**: `input_validate` and the `security_gate_output` (S-3) declare
`required_trust_level = VERIFIED_EXTERNAL`, so a verified caller is required to reach the workflow
(procurement objective / budget is business-confidential). The **inner** nodes run inside that
boundary and declare `required_trust_level = ANONYMOUS` (explicitly): the framework's
`BaseGraph.invoke()` does not propagate `caller_trust_level` into the inner graph state, so an inner
`VERIFIED_EXTERNAL` would be unreachable — and re-gating trust there would be redundant, exactly as
S-2 is not duplicated on the trusted inner handoff.

### Outer graph (`src/graph/graph.py`, `AgentBaseGraph`)

Fixed 5-node backbone; `register_nodes()` fills `pre_process` / `main` / `post_process` (+ `super()`
injects `initialize` / `finalize`). `add_edges()` is **not** overridden.

| Slot | Node | Responsibility | Inherits/Overrides |
|------|------|---------------|-------------------|
| initialize | InitializeNode | schema_version, correlation setup | default |
| pre_process | InputValidateNode | **S-2** (`_extra_security_gate_input()`): raw-string structural screen (non-str/binary/NUL/excessive-control/oversized); structured-data-only posture (no raw PII), tuned so legitimate vendor/person names are not false-positive-blocked. **execute()**: require caller-input `procurement_objective, budget`; resolve `procurement_type` / `law_jurisdiction` (per-call input, else `config/config.yaml` default); reject only if `procurement_objective` / `budget` is missing or `procurement_type` stays unresolved (no per-call value and no config default); per-field injection screen of the NL requirement text (`procurement_objective` / `technical_requirements`: reject control chars other than tab/newline/CR); **enforce `advisory_only`** (code-constant; a config `false` is rejected) | FunctionNode |
| main | ProcurementWorkflowGraphNode | **`GraphNode`** wrapping the inner `DomainWorkflowGraph`: `get_subgraph()` returns it, `extract_input()` returns `validated_input` JSON-encoded as a **string**, `merge_output()` (success path) writes the inner `output` into the inherited `result` (+ `audit_metadata`) — nothing else. `error_strategy = "propagate"`; `execute()` catches the framework-raised `SubgraphError` on an inner failure and returns a `status: error` delta (no partial output) carrying only the allowlisted `stg_error` classification. S-2/S-3 are a deliberate no-op here (gating is on the outer pre/post) | GraphNode |
| post_process | SecurityGateOutputNode | **S-3** (in `execute()`, which receives the full state): inject the advisory disclaimer into the composed output; **reject if `advisory_only` has been tampered to false**; write the gated draft to the inherited `formatted_output` so it reaches the caller (`get_output = formatted_output or result`). The framework `@final` credential scan still runs. Emits an **S-4** `emit_trace_event` (`output_gated`); the `audit_metadata` artifact is assembled by the inner `audit_metadata_emit` | FunctionNode |
| finalize | FinalizeNode | response_metadata, node_history | default |

### Inner graph (`src/graph/domain_workflow_graph.py`, `BaseGraph`)

Custom linear topology; the domain pipeline. Each node emits at least one S-4 domain trace event.

| Node | Responsibility | Input State | Output State |
|------|---------------|-------------|--------------|
| input_hydrate | **Hydration (first inner node):** JSON-decode the inner `user_input` (the string handed off by the outer `extract_input()`) into the inherited `validated_input`; shape/format confirmation only — **no duplicated S-2** (the outer `input_validate` already gated the input; trusted handoff). `BaseGraph.invoke()` sets only inner `user_input`, not `validated_input` | user_input | validated_input |
| requirements_normalize | **LLM**: parse NL into a structured requirement schema (Purpose / Scope / Technical Requirements / Evaluation Criteria / Timeline / Budget Ceiling) | validated_input | normalized_requirements |
| procurement_law_compliance_check | **Deterministic rule engine** (`common_error_rules`): check against 会計法 / 地方自治法 conventions for the active `law_jurisdiction`; flag vague requirements, missing delivery timeline, absent acceptance criteria | normalized_requirements | compliance_flags |
| specification_draft | **LLM**: generate a 総務省 / 内閣府-format 仕様書, grounded on `procurement_law_kb` for regulatory language | normalized_requirements, compliance_flags | spec_draft |
| compliance_clause_inject | Inject mandatory clauses (入札公告, 前払金 limit, 検収条件, APPI/個人情報保護 handling), **selected by `law_jurisdiction`** (会計法 central / 地方自治法 municipal) **and `procurement_type`** (議会議決 is required only for a 地方自治法 工事又は製造の請負 — construction/manufacturing — per 地方自治法第96条; not for goods/services), **GROUNDED on the committed bundled `compliance_clause_library`** (authoritative text + source per clause; version projected as `kb_version` for the audit). A mandatory clause whose text is unavailable **fails closed** (`status: error`) — never an ungrounded placeholder draft | spec_draft | spec_with_clauses, clause_list, kb_version |
| review_checklist_generate | Generate a reviewer checklist (compliance gaps, missing fields, items needing legal review); **mark every AI-generated item as requiring human validation** (国家賠償法) | spec_with_clauses, compliance_flags | reviewer_checklist |
| audit_metadata_emit | **S-4 artifact (terminal inner node):** assemble `audit_metadata` `{data_hash, generated_at, kb_version, model_version, law_jurisdiction}`; raw bodies not persisted (hashes only). The inner `get_output()` returns `{output: <仕様書 + clauses + checklist>, audit_metadata}` | reviewer_checklist, spec_with_clauses, clause_list | audit_metadata |

### Data Flow

```
Outer backbone (fixed — add_edges NOT overridden):
  START → initialize → pre_process (InputValidate) → main (ProcurementWorkflowGraphNode) → {route}
        → post_process (SecurityGateOutput) → finalize → END
                              ↓ (RETRY, max 3)
                           pre_process

Inner DomainWorkflowGraph (invoked by the main GraphNode):
  START → input_hydrate → requirements_normalize → procurement_law_compliance_check → specification_draft
        → compliance_clause_inject → review_checklist_generate → audit_metadata_emit → END
```

> The outer `AgentBaseGraph` does **not** override `add_edges()` — the framework owns the backbone
> and the RETRY route. The `main` `GraphNode` (`ProcurementWorkflowGraphNode`) calls
> `inner_graph.invoke(extract_input(state), session_id=ctx.session_id, ctx=ctx)` (a **string** — the JSON-encoded outer `validated_input`); the inner graph runs its own topology and returns
> `sub_result` via its `get_output()`; `merge_output()` maps the changed keys back into the outer
> state. The inner graph lives at `src/graph/domain_workflow_graph.py` (not `src/subagents/`).

### State Definition

Two state schemas (Cat 2 two-layer). Framework fields are **inherited from `AgentState`** (which
extends `SubgraphContext`) and are **NOT redeclared** — notably `user_input: str` (raw caller payload
as a string), `validated_input`, `result` / `formatted_output` (terminal output), `status`,
`session_id`, and the reducer-managed `node_history` / `error_log`.

**Input contract:** the caller passes the procurement request as a **string** in `user_input` (a JSON
string) — `{procurement_objective, budget, timeline, technical_requirements, procurement_type,
law_jurisdiction}`. The outer `input_validate` parses/validates it and enforces `advisory_only` into
the inherited `validated_input`. The `main` GraphNode's `extract_input()` returns `validated_input`
JSON-encoded as a **string** to the inner graph (`extract_input(state) -> str`). The inner graph receives this string as its own
`user_input`; the first inner node `input_hydrate` JSON-decodes it back into the inherited
`validated_input` (`BaseGraph.invoke()` sets only inner `user_input`, not `validated_input`).

**Outer State** (`src/schemas/state.py`, `State(AgentState)`) — only outer-specific fields:

| Field | Type | Purpose | Set by |
|-------|------|---------|--------|
| audit_metadata | NotRequired[dict] | `{data_hash, generated_at, kb_version, model_version, law_jurisdiction}` — projected from the inner workflow | merge_output (from inner) |

The final S-3-gated 仕様書 + clause list + reviewer checklist is written to the **inherited
`formatted_output`** (via `security_gate_output.execute()`, which receives the full state);
`AgentBaseGraph.get_output()` returns `formatted_output or result`.

**Inner State** (`src/graph/domain_workflow_graph.py`, `DomainWorkflowState(AgentState)`) — the domain
pipeline fields (schema created in the inner-graph issue; named + owned here):

| Field | Type | Purpose | Set by |
|-------|------|---------|--------|
| normalized_requirements | NotRequired[dict] | `{purpose, scope, technical_requirements, evaluation_criteria, timeline, budget_ceiling}` | requirements_normalize |
| compliance_flags | NotRequired[list[dict]] | Rule-based specification-defect flags `{rule, severity (High/Medium/Low), field, message}` | procurement_law_compliance_check |
| spec_draft | NotRequired[str] | 総務省 / 内閣府-format 仕様書 draft (Markdown) | specification_draft |
| spec_with_clauses | NotRequired[str] | Draft with mandatory clauses injected | compliance_clause_inject |
| clause_list | NotRequired[list[dict]] | Injected clauses `{id, title, source, jurisdiction, missing}` — every mandatory clause is grounded on the bundled library text (`missing: false`); a mandatory clause with absent text **fails closed** (no draft). `missing: true` is used only for the `unrecognized_jurisdiction` review flag (not a clause with library text) | compliance_clause_inject |
| kb_version | NotRequired[str] | The clause-library revision used (bound into the audit `kb_version`, tracing the output to its legal-content source) | compliance_clause_inject |
| reviewer_checklist | NotRequired[list[dict]] | `{item, category, human_validation_required}` | review_checklist_generate |
| audit_metadata | NotRequired[dict] | `{data_hash, generated_at, kb_version, model_version, law_jurisdiction}` — S-4 artifact, projected to outer | audit_metadata_emit |
| stg_error | NotRequired[str] | Allowlisted failure classification `"NodeName: ErrorClass"` for the Stage 5 deploy-stg diagnostic — projected **on a failed run only**; never a raw error message, response, input, or secret (`src/services/stg_error`) | main GraphNode `execute()` (from inner error_log) |

**Projection (success vs failure):** on success the inner `get_output()` returns
`{output: <仕様書 + clauses + checklist>, audit_metadata: …}` and the `main` GraphNode's `merge_output()`
writes `output` into the outer inherited `result` and copies `audit_metadata`. On failure the inner
`get_output()` returns `{status: error, error_log}`; under `error_strategy = "propagate"` the framework
GraphNode **raises `SubgraphError`** (no partial output) and `merge_output()` is **not** called on that
path. The `main` GraphNode's `execute()` catches `SubgraphError` and returns a `status: error` delta
(same observable contract — no `result`) that additionally projects an allowlisted failure classification
`stg_error` (`"NodeName: ErrorClass"`, derived from the inner `error_log` via `src/services/stg_error`),
which the outer `get_output()` surfaces in the response for the Stage 5 deploy-stg diagnostic. The **raw**
`error_log` / `node_history` are reducer-managed and are **NEVER** projected inner → outer — only the
sanitized `stg_error` token is.

**Input-contract / config precedence (finalized here):** `procurement_type` and `law_jurisdiction`
may be supplied per-call in `user_input` OR set as `config/config.yaml` defaults; **the per-call input value
takes precedence**, falling back to the config default (zero-config uses the config value). Mandatory
**caller-input** fields are `procurement_objective` and `budget`. `procurement_type` must be
**resolved** — from per-call input or the config default — and `input_validate` rejects only if it
stays unresolved; `law_jurisdiction` and the rest are optional.

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (LLM key via `ctx.secrets.require()`, never stored)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)
- Structured procurement data only; no raw PII persisted in State

## Config Surface (config/config.yaml)

Runtime-tunable values live in `config/config.yaml`, which AgentRegistry loads separately
and passes as `Graph(config=...)`, and which `src/api/server.py` loads the same way on the
standalone path. The static discovery manifest `config/agent.yaml` carries no runtime
parameters.

```yaml
advisory_only: true                                # MANDATORY — code-constant hardlock; a config false is rejected
procurement_type: IT                               # IT | 物品 | 役務 | 工事  (per-call input overrides)
law_jurisdiction: 地方自治法                          # 会計法 (national/central) | 地方自治法 (municipal)
output_format: markdown                            # markdown is the only V1 output format (not read as a live selector); docx = future scope (see DDR)
kokka_baisho_disclaimer: true                      # RESERVED (future scope) — a dedicated 国家賠償法 disclaimer line is not yet wired; 国家賠償法 risk mitigation is delivered by the always-on advisory disclaimer + the reviewer checklist marking AI items for human validation (see DDR)
procurement_law_kb: config/procurement_law_kb/
# compliance_clause_library: bundled base is committed (src/services/clause_library_data.py); a
# deployment MAY override it here with a resolved {clauses, version} mapping.
specification_format_templates: config/specification_format_templates/   # RESERVED — the V1 draft uses a fixed in-code structure; per-type format templates are future scope (see DDR)
common_error_rules: config/common_error_rules.yaml
```

- **`advisory_only` is a code-side constant, not a config toggle.** A configuration attempting to set
  it `false` is rejected (`ConfigError`); the advisory disclaimer and the S-3 tamper check always
  apply. Enforcement lives at **both** `input_validate` (input header enforcement) and
  `security_gate_output` (S-3 output gate).
- **Zero-config behaviour:** with no overrides, `procurement_type` / `law_jurisdiction` take their
  config defaults, `output_format: markdown`, and the bundled KB / clause library apply. The reviewer
  checklist is **always generated** (`review_checklist_generate` is an unconditional pipeline node —
  there is no config toggle to disable it).
- **`audit_metadata` is an always-produced, invocation-local artifact.** `audit_metadata_emit` runs on
  every invocation and its minimal hash-only artifact (data hash, `generated_at`, `kb_version` / model
  version, `law_jurisdiction`) is produced and returned with `result`; raw procurement bodies are never
  stored in the artifact (hashes only). V1 does not emit to any external/persistent audit-log sink.

## KB / Data Files

- `procurement_law_kb/` — 会計法, 地方自治法, 総務省 / 内閣府 format patterns (vector store). Bundled base
  (総務省 published templates, METI procurement guidelines) is authored by the implementer;
  municipality-specific past-tender examples are **supplied by the customer**.
- `compliance_clause_library` — mandatory clauses per jurisdiction; **holds both the 会計法 (central)
  and 地方自治法 (municipal) regimes**, selected via `law_jurisdiction` (入札公告 / 前払金 / 検収条件 /
  個人情報保護 …). The bundled base is **committed as `src/services/clause_library_data.py`**
  (authoritative text + `source` per clause + a `BUNDLED_VERSION`), so the mandatory clauses are
  grounded on committed text and each output is traceable to the exact revision via the audit
  `kb_version`. A deployment MAY override it (a resolved mapping with `clauses` / `version`); a
  malformed override or a missing mandatory clause fails closed.
- `specification_format_templates/` — **reserved (future scope)**: per ministry / municipality
  header/footer/format blocks. V1 uses a single fixed in-code draft structure (see DDR).
- `common_error_rules.yaml` — deterministic common-defect patterns used by the compliance check.
- **Update ownership:** legal-text / clause updates on law change (会計法 / APPI) are the deploy
  operator's responsibility — staleness maps directly to 国家賠償法 (State Redress Act) liability.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle)
- [x] ConnectionPolicy (retry/timeout for the LLM calls)
- [x] **LLM adapter injection** — the adapter travels through the graph config under `llm`;
      `graph.py` prefers a supplied adapter and otherwise injects the default keyless
      `AzureOpenAIDraftLLM`, so a config that declares no `llm` still has a working adapter. It resolves
      `AZURE_OPENAI_*` from the **request-bound `SecretProvider`**
      (`InvocationContext.from_state(state).secrets.require`) at CALL time — never `os.environ`,
      never State, never a build-time read; an unprovisioned key fails closed. `src/api/startup.py`
      is the single startup boundary that loads `config/config.yaml`, selects the deployment
      profile and compiles, so no node reads the process environment. Provisional Stage ⑤
      substitutes a bounded fake for the provider call under an explicit CI-only flag, so it needs
      no credential and proves the workflow contract rather than a live call (operation guide,
      *Provisional STG evidence*).
- [x] SecurityViolationError
- [x] **S-2** `_extra_security_gate_input()` (on `input_validate`): raw-string structural screen —
      reject non-str / binary / NUL / excessive control characters / oversized payloads. Per-field
      structural injection screening of the NL requirement text (`procurement_objective`,
      `technical_requirements`: reject control characters other than tab/newline/CR, multi-line NL
      allowed) runs in `execute()` after the JSON is parsed, where the field is identifiable.
      Structured-data-only posture (no false-positive block of legitimate vendor names). Semantic
      prompt-injection is neutralized at the inner LLM boundary. **`_security_gate_input()` is NOT
      overridden.**
- [x] **S-3** (in `security_gate_output.execute()`, which receives the full state): inject the
      advisory disclaimer; reject if `advisory_only` is tampered to false; write the gated draft to
      `formatted_output`. The framework `@final` `_security_gate_output()` credential scan still runs
      and is **NOT overridden**. **Credential BLOCK posture:** a credential-shaped string
      in any node's `execute()` return — including LLM-generated draft prose — trips the baseline
      `@final` scan, which runs *before* and *independent of* this domain gate, and fails closed
      (`status: error`, no output, credential body never surfaced). BLOCK is the approved end-to-end
      behaviour; there is **no** downstream redaction-to-success, and the disclaimer/`formatted_output`
      handoff is never reached on that path.
- [x] **S-4** `emit_trace_event()` — at least one domain event inside each `execute()` (e.g.
      `input_validated`, `requirements_normalized`, `compliance_checked`, `spec_drafted`,
      `clauses_injected`, `checklist_generated`, `output_gated`). Framework
      `node_start`/`node_complete`/`node_error` are **not** re-emitted.

> **S-2/S-3 gate behaviour (ADR-017):** every `FunctionNode` runs the framework `@final`
> `_security_gate_input()` / `_security_gate_output()` baseline gates automatically. The
> template-specific **S-2** input check extends via `_extra_security_gate_input()` (on the outer
> `input_validate`); the template-specific **S-3** advisory-disclaimer injection + `advisory_only`
> tamper reject and the `formatted_output` handoff run in `SecurityGateOutputNode.execute()` (which
> receives the full state), **not** via `_extra_security_gate_output()`. The `main` `GraphNode`'s
> S-2/S-3 are a **deliberate no-op** — input gating is done by the outer `pre_process`, output gating
> by the outer `post_process`.

### Composition Pattern
- **Pattern**: **GraphNode (subgraph)** — the `main` slot wraps the inner `DomainWorkflowGraph`.
- **Composition target**: `src/graph/domain_workflow_graph.py` (`BaseGraph`, custom topology).
- **Error propagation strategy**: **propagate** (`error_strategy = "propagate"`) — inner-graph errors
  re-raise as `SubgraphError`; the outer graph short-circuits to `finalize` with `status: error`; no
  partial specification is emitted. No in-graph HITL (`propagate_hitl` is a GraphNode-subclass opt-in, not used here).

## Outcome Classification

A run ends in one of three ways, and which one is decided by **whose problem the condition is**.

| Outcome | Conditions | What the caller receives |
|---|---|---|
| Specification draft | the request was accepted and drafted | the gated draft (advisory disclaimer always present) in `output` |
| Notice (`status: success` + `diagnostic_code`) | the request was read and found correctable: more content than the ceiling allows, a body that is not a JSON object, a missing or unreadable procurement objective or budget, a per-field length or character contract, a procurement type or law jurisdiction that cannot be resolved, or an optional field that does not match its documented type | one sentence naming what to change, in `output`, with the allow-listed code in `diagnostic_code`. **No draft is built** |
| Error (`status: error`, no output) | the screen that decides whether the body is a document at all (non-text input, a NUL byte, a flood of control characters); the terminal S-3 gate (the advisory posture is not negotiable); and every condition inside the workflow — a broken handoff, an unusable drafting model, a compliance-rule fault, or a mandatory clause whose source text is absent from the clause library | nothing. The operator-facing reason stays in `error_log` |

A specification draft with no content is **not** returned in place of a notice: that would be a
real artifact for a request this agent never read.

`procurement_type_unresolved` is answered for the same reason as the rest: whatever the deployment
default is, the caller can name the type in the request and send it again.

The size ceiling is answered rather than refused because it is a limit the caller can work within.
It is screened before `execute()`, so the answer carries the code and no `user_input`, and
`execute()` returns on seeing it — an oversized body is never parsed. The non-text screen next to
it keeps refusing: it exists so such a payload is never interpreted at all.

Only the outer `pre_process` produces a notice, so no marker has to cross the inner graph boundary:
`main` skips the workflow when the gate has already answered, and `post_process` releases the
sentence. The wording comes from the single `failure_message` catalogue, which substitutes only the
field name the code itself carries — no request value, procurement objective or budget figure can
appear.

### Where the advisory posture is carried

`input_validate` writes `advisory_only: true` **inside `validated_input`**, and nothing writes a
top-level `state["advisory_only"]`. The terminal S-3 tamper check therefore reads
`validated_input["advisory_only"]`, and treats an absent or reshaped `validated_input` as not
intact — by the time a draft reaches that gate the canonical `validated_input` must be there. A
check keyed on the top-level name would inspect a field that is always absent and could never fire.

## Error & Edge Cases

| Condition | Behaviour |
|-----------|-----------|
| `procurement_objective` / `budget` missing, or `procurement_type` unresolved (no per-call input and no config default) | `input_validate` → **notice** (`status: success` + `diagnostic_code`), no draft — the caller can name the type in the request |
| Request larger than `max_input_chars` | S-2 → **notice** (`s2_oversized`); the body is never parsed |
| Non-text / binary input (NUL byte, control-character flood) | S-2 **reject** (`status: error`), `execute()` never runs |
| Control characters in the NL requirement text or in a single-line key | `input_validate` → **notice**; a contract on a value inside a body already accepted as text |
| `advisory_only` set to `false` in config | rejected (`ConfigError`); the constant remains true |
| Non-mandatory NL under-specification | proceed with assumptions flagged in the reviewer checklist (no in-graph HITL) |
| `law_jurisdiction` non-empty but unrecognized (not 会計法 / 地方自治法) | surfaced, not silently swallowed: `procurement_law_compliance_check` adds an `unrecognized_jurisdiction` flag (High) and `compliance_clause_inject` adds an `unrecognized_jurisdiction` review flag; the common clauses remain grounded so the base draft is still produced (not a hard error). Empty `law_jurisdiction` = zero-config (base rules / common clauses) |
| Mandatory clause text unavailable in the clause library (a required clause id has no grounded text) | `compliance_clause_inject` → **fail closed** (`DomainComputeError` → `status: error`), no draft — a specification with required legal text absent must not be returned as a usable human-review draft |
| LLM unavailable (`requirements_normalize` / `specification_draft`) | inner-graph error → `status: error`, no output — the deterministic `procurement_law_compliance_check` depends on the LLM `normalized_requirements`, so no partial flags are emitted (`error_strategy = propagate`) |
| `advisory_only` not intact at output (the value inside `validated_input` is absent, reshaped, or not `true`) | `security_gate_output` (S-3) **rejects** — see *Where the advisory posture is carried* above |
| Credential-shaped content reaches a node's `execute()` return (procurement text passed through, or an LLM-generated draft) | framework `@final` S-3 credential scan **BLOCKS** fail-closed → `status: error`, no output; credential body not returned in `result` / response / trace / `stg_error`. Approved end-to-end posture — not softened by any downstream redaction-to-success, since baseline S-3 runs before and independent of `security_gate_output` |
| Any invocation | `audit_metadata_emit` always runs and the invocation-local hash-only `audit_metadata` is produced and returned with `result`; V1 emits to no external/persistent audit-log sink |

## Guardrails & Human-Review Boundary

- Output is a **draft for human review**, not a regulatory submission and not a final legal
  instrument. The procurement officer retains sole legal responsibility.
- **No auto-submission** to e-調達 procurement systems (out of scope).
- The reviewer checklist marks every AI-generated item as requiring human validation (国家賠償法).
- **In-graph HITL is NOT required** — submission is out of scope, so the human validates externally.
  (Were in-graph HITL ever required, it would need `hitl.enabled: true` + `memory_enabled: true` and
  a PB-7 interrupt-propagation test.)
- **Scope boundary:** this template is **generation-first** (NL → specification draft). Standalone
  compliance validation of an already-drafted specification is out of scope.
- Read-only: no external writes.

### Deterministic procurement rules (fixed; no LLM)

- **`procurement_law_compliance_check`** (rule engine, `common_error_rules` overrides the code
  baseline): flags missing purpose/scope, missing or vague technical requirements (a requirement
  shorter than `min_requirement_len` is vague), absent acceptance/evaluation criteria, missing
  delivery timeline / budget ceiling. A malformed `common_error_rules` threshold
  (`min_requirement_len` / `min_requirements` — expected positive integers) is a data fault →
  **fail closed** (`DomainComputeError` → the node's `status: error`), never coerced or compared
  as-is (which would raise an uncaught `TypeError`). Jurisdiction-specific: 地方自治法 (municipal) additionally
  requires an explicit 検収 basis. A **non-empty but unrecognized `law_jurisdiction`** (not one of
  会計法 / 地方自治法) is surfaced as an `unrecognized_jurisdiction` flag (High) — jurisdiction-specific
  checks are not applied, so coverage is incomplete and human confirmation is required; it is never
  silently swallowed. An empty `law_jurisdiction` is the zero-config case (base rules only). Each
  flag is `{rule, severity (High/Medium/Low), field, message}` — decision-support, never approval.
  A non-object requirement schema is a data fault → fail closed.
- **`compliance_clause_inject`**: the REQUIRED mandatory clause set is a **code baseline** (common:
  入札公告 / APPI・個人情報保護 / 検収条件; 会計法 → 前払金限度額〔国〕; 地方自治法 → 前払金限度額〔自治体〕),
  selected by `law_jurisdiction` **and `procurement_type`** — 議会議決 要否 is added only for a 地方自治法
  工事又は製造の請負 (construction/manufacturing) per 地方自治法第96条, not for goods/services. Clause TEXT is
  sourced from the bundled `compliance_clause_library`; a required clause with **no grounded library
  text FAILS CLOSED** (`DomainComputeError` → `status: error`), never an ungrounded placeholder draft.
  A **non-empty but unrecognized `law_jurisdiction`** selects no jurisdiction-specific clause set; that
  gap is surfaced through the `unrecognized_jurisdiction` review flag (the common clauses remain
  grounded so the base draft is still produced), not silently reduced or failed.
- **`review_checklist_generate`**: one checklist item per compliance flag plus the standard
  legal-review items; **every item is `human_validation_required: True`** (国家賠償法 boundary).

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only (no `agents/base/` required)

## Acceptance Criteria (testable)

- Valid NL input → structured 仕様書 (Markdown) + compliance-clause list + reviewer checklist.
- `advisory_only: false` attempt → rejected (`ConfigError` at load / S-3 block); disclaimer always present.
- Injection in requirement text → S-2 reject.
- Missing `procurement_objective` / `budget`, or `procurement_type` unresolvable (no per-call value, no config default) → `input_validate` reject.
- `procurement_type` omitted but present as a `config/config.yaml` default → accepted (zero-config uses the config value).
- Dual jurisdiction: `law_jurisdiction: 会計法` vs `地方自治法` → the correct clause set is injected.
- Legitimate vendor/person name in input → **not** false-positive-blocked by S-2.
- `audit_metadata` is always produced and returned with `result` (`audit_metadata_emit` runs on every invocation; the invocation-local hash-only artifact — no external sink in V1).
- **PB**: the S-3 `advisory_only` gate (advisory disclaimer in all outputs; reject on tamper) verified end-to-end.
- **PB**: credential-shaped content in an LLM-generated draft → full outer `invoke` returns `status: error`, `get_output()["output"] is None`, and the credential body is not surfaced (framework S-3 BLOCK; no downstream redaction-to-success).

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed deterministic pipeline, not an autonomous think-act loop |
| Credential-shaped content in output | Redact-to-SUCCESS (downstream mask) | BLOCK (fail-closed at baseline S-3) | **BLOCK** | Baseline `@final` S-3 runs before `security_gate_output` and raises on match, so a downstream mask-to-success is unreachable end-to-end; a procurement draft carrying a credential-shaped token is safer returned fail-closed (`status: error`) for human follow-up than masked-and-shipped |
| Graph class name | `…Agent` | `…AgentGraph` | **`…Agent`** | Matches the fixed design spec; some fleet templates suffix `…Graph` |
| Composition pattern | flat FunctionNode chain (`add_edges` override) | outer backbone + inner `BaseGraph` via `main` GraphNode | **outer backbone + inner GraphNode** | Framework Cat 2 pattern: fixed outer backbone, domain workflow in an inner graph, `add_edges()` not overridden |
| `advisory_only` trust anchor | config value | code-side constant, config-non-overridable | **code-side constant** | A config value is undetectably tamperable at S-3 (same config); enforced at input + output |
| NL under-specification | HITL `interrupt()` | reject mandatory / flag the rest | **reject mandatory; flag the rest** | Submission out of scope → no in-graph HITL; soft gaps flagged in the checklist |
| Dual jurisdiction | single hardcoded set | both regimes, selected by `law_jurisdiction` | **both, selected by `law_jurisdiction`** | 会計法 (central) and 地方自治法 (municipal) clause sets differ; must not hardcode one |
| `procurement_type` / `law_jurisdiction` | config only | per-call input overrides config | **per-call input overrides config** | Zero-config uses the config default; a per-call value wins |
| Checklist generation | full free-LLM | skeleton + dynamic fill (grounded) | **skeleton + dynamic fill** | Grounded on `common_error_rules`; auditable, not free-form |
| Output format | docx now | markdown v1, docx later | **markdown v1** | docx deferred to a follow-up scope |
| PII / APPI posture | scan-and-block all | structured-data-only; tuned S-2 | **structured-data-only; tuned S-2** | Vendor/person names may legitimately appear; APPI clauses are injected, not blocked |
| `kokka_baisho_disclaimer` (dedicated 国家賠償法 disclaimer line) | inject the line in V1 | reserved; mitigation via checklist + always-on disclaimer | **deferred (reserved config)** | 国家賠償法 risk mitigation is delivered by the always-on advisory disclaimer and the reviewer checklist marking every AI-generated item for human validation (per the proposal's ReviewChecklistGenerate); a dedicated disclaimer line is future scope |
| Audit-trail sink | wire an external sink in V1 | always-on invocation-local artifact | **always-on invocation-local (no external sink in V1)** | The audit-trail requirement is satisfied by `audit_metadata_emit` (always-on, invocation-local, hash-only); an external/persistent sink is future scope |
| `specification_format_templates` KB | per-type format-template files | fixed in-code draft structure | **fixed structure (templates reserved)** | The V1 draft uses a single deterministic structure; per-`procurement_type` format templates are future scope |
