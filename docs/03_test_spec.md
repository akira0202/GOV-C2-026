# Test Specification

## Test Strategy
- **Test types:** Unit (framework-free domain / generation services + node deltas) · Integration (graph
  config wiring) · Proof-of-Boundary (framework contract) · End-to-end (`Graph.invoke()` through the
  outer backbone + inner workflow + S-3).
- **Coverage approach:** every testable Acceptance Criterion in `docs/02_design.md` maps to one or more
  tests (see Business Logic Tests below); line-coverage is not a CI gate — criterion coverage + the
  mandatory Framework-Compliance / Proof-of-Boundary items are the target.
- **Non-determinism:** the only non-deterministic step is the LLM in `requirements_normalize` /
  `specification_draft`. It is isolated structurally — a fixed system message + the untrusted data in a
  separate user message (JSON, `ensure_ascii`); every LLM result is schema-validated and fails closed on
  a malformed / missing / unsafe response; the S-3 advisory disclaimer is node-owned, never LLM-supplied.
  Tests inject fake / absent / malformed LLMs; no live model call is made in CI.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | Pass (`test_state_file_safety`) |
| TC-02 | The S-2 boundary refuses what may not be parsed at all | Refused before `execute()` — non-text / NUL / control-character flood; a request the caller can correct is answered instead (`status: success` + `diagnostic_code`), never silently accepted | Pass (`test_pb_input_gate::TestS2InputGateBoundaryRejection`, `test_input_validate_node::TestS2Gate`, trust gate) |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations (S-5 enforcement is done in CI) | Pass (CI gate) |
| TC-04 | InvocationContext via configurable only | Direct access raises error | Pass (S-3 secret resolved via ctx: `test_fails_closed_without_bound_secret`; `test_no_raw_body_or_llm_in_delta`) |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused | Pass (outer `test_trust_levels`; inner `test_all_inner_anonymous`) |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial when domain checks needed | Domain-specific input checks execute correctly (schema, injection screen, type contracts) | Hook body non-trivial |
| TC-10 | S-3: template-specific output gate runs in `SecurityGateOutputNode.execute()` (not `_extra_security_gate_output()`) | advisory-disclaimer injection + `advisory_only` tamper reject execute correctly; gated draft written to `formatted_output`; framework `@final` `_security_gate_output()` baseline still runs | execute()-based S-3 verified |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | Domain event emitted on every invocation path | ≥1 per node (`*_emits_domain_event`) |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | Pass (`test_call_order_for_every_node`; per-node `*_emits_domain_event`) |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | Pass (`test_state_file_safety`) |
| PB-3 | L1 → External service | Real external service connection | Data retrieved | N/A — no external service in scope (deterministic clause library is bundled; the LLM draft is optional / injected, exercised via fakes) |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | Pass (`test_no_prohibited_imports_in_src`; CI `gate-import-isolation`) |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | Pass (`test_state_file_safety` — flat JSON-serializable State) |
| PB-6 | Invoke execution order | `__call__()`: S-1 trust gate → S-4 `node_start` → S-2 `_security_gate_input` → `execute()` → S-3 `_security_gate_output` → S-4 `node_complete` | Order verified | Pass (`test_call_order_for_every_node`) |

*PB-7 (HITL interrupt propagation) is not applicable — `hitl.enabled` is not set (submission is out of
scope; soft gaps are flagged in the checklist, not via `interrupt()`). The required exact-name stub
`test_pb7_hitl_interrupt_propagation.py` is present and auto-skips (it activates only when `hitl.enabled: true`).*

**Workflow-boundary proofs (end-to-end `Graph.invoke`).** `test_pb_workflow.py` exercises the deployed
outer boundary of the approved workflow:
- **Required human-review posture reaches the caller** — the advisory disclaimer is present, the reviewer
  checklist marks every item `human_validation_required: true`, and the always-on `audit_metadata`
  artifact reaches the caller (surfaced by the outer `get_output()` override).
- **Invalid / partial input fails closed** — malformed input and a request missing the mandatory `budget`
  each return `status: error` with no partial output (`error_strategy = propagate`).
- The workflow composition runs in order (inner `ProcurementWorkflowGraphNode` → outer `SecurityGateOutputNode`).
- **Credential-shaped content in the LLM-generated draft is BLOCKED end-to-end** — the framework `@final`
  S-3 credential scan trips on the `specification_draft` node's return; the outer `invoke` returns
  `status: error`, `get_output()["output"] is None`, and the credential body is not surfaced (BLOCK
  posture; no downstream redaction-to-success).

**Security-boundary proofs (S-2 / S-3).** Beyond the invoke-order proof (PB-6), the S-2 input gate and
S-3 output gate are proven at the `__call__` boundary — observable behavior, not fixture presence:
- `test_pb_input_gate.py` — the S-2 structural gate rejects non-text / binary / NUL / control-char /
  oversized raw `user_input` *before* `execute()` runs (each rejection spies `execute()` and asserts it
  was **not** invoked; a clean payload is the negative control and reaches `execute()`); a legitimate
  vendor/person name in the NL objective is not over-blocked.
- `test_pb_output_gate.py` — the S-3 advisory gate injects the node-owned disclaimer into every output
  (string or dict), blocks an `advisory_only` tamper (`status: error` **and** `output_gated{blocked:true}`),
  emits exactly one `output_gated` event, and rejects a config `advisory_only:false` at construction.
- Prohibited-import isolation (PB-4) and State safety (PB-2/5) remain enforced by
  `test_no_prohibited_imports_in_src` and `test_state_file_safety`.

## Business Logic Tests

Every row maps to a `docs/02` Acceptance Criterion or Error & Edge case. Test references are
`file::function` (unit tests under `tests/unit/`).

| BL-ID | Test (case type) | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Valid NL → 仕様書 + clause list + checklist (normal) | valid procurement objective + budget + requirements | structured Markdown 仕様書 + grounded compliance-clause list + reviewer checklist; outer invoke returns a non-null gated draft | Pass (`test_domain_nodes::test_full_flow`, `test_generation_nodes::test_normalize_then_compliance_then_draft`, `test_graph_config_wiring::test_invoke_reaches_inner_workflow_and_gates_output`) |
| BL-02 | `advisory_only` is non-negotiable (invalid-input / tamper) | `advisory_only: false` at config load / per-call / runtime graph / S-3 output | rejected at load (`ConfigError`); per-call `false` ignored; graph registration rejects; S-3 blocks tamper | Pass (`test_input_validate_node::test_config_false_raises_configerror_at_load/test_payload_false_is_ignored_not_disabled`, `test_generation_nodes::test_config_advisory_only_false_rejected/test_execute_blocks_advisory_only_tamper`, `test_graph_config_wiring::test_registration_rejects_runtime_advisory_only_false/test_registration_rejects_runtime_advisory_only_false_at_output_gate`) |
| BL-03 | Advisory disclaimer always present (S-3, human-review) | string draft / dict draft / other | node-owned advisory disclaimer prepended/added idempotently; never LLM-dependent | Pass (`test_generation_services::test_disclaimer_prepended_to_string/test_disclaimer_idempotent/test_disclaimer_field_added_to_dict/test_has_disclaimer`, `test_generation_nodes::test_execute_writes_disclaimer_to_formatted_output`) |
| BL-04 | `procurement_type` / `law_jurisdiction` resolution (normal / boundary) | config default only / per-call override / neither | config default resolves; per-call wins; unresolved → reject | Pass (`test_graph_config_wiring::test_config_default_resolves_through_the_graph_path/test_per_call_still_overrides_the_wired_config/test_unresolved_when_no_config_default_and_no_per_call`, `test_input_validate_node::test_valid_json_with_config_fallback/test_per_call_overrides_config/test_procurement_type_unresolved`) |
| BL-05 | Missing / invalid mandatory fields (invalid-input) | missing objective / budget / non-positive budget / malformed JSON / non-object / unresolved procurement_type | **answered**: `status: success` + `diagnostic_code`, one sentence naming what to change, no `validated_input` and no draft — and the inner workflow does not run (`audit_metadata` absent), so an empty specification is never returned in its place | Pass (`test_input_validate_node::TestRejections`, `test_graph_config_wiring::TestGraphConfigWiring::test_unresolved_when_no_config_default_and_no_per_call`, `test_pb_workflow::TestACorrectableRequestIsAnsweredEndToEnd`) |
| BL-06 | Dual jurisdiction clause selection (normal) | `law_jurisdiction: 会計法` (central) vs `地方自治法` (municipal); type = 工事 / others | correct common + regime clause set; `議会議決` only for construction/manufacturing; central never adds 議会議決 | Pass (`test_domain_services::test_central_jurisdiction_clauses/test_common_and_municipal_clauses_selected/test_gikai_giketsu_only_for_construction_or_manufacturing/test_central_no_gikai_regardless_of_type/test_municipal_jurisdiction_inspection_basis`) |
| BL-07 | Mandatory clause grounding — fail-closed (error-path) | full bundled library / missing mandatory clause / partial library / ungrounded override | grounded clause text appended; a missing/partial mandatory clause fails closed (no draft) | Pass (`test_domain_services::test_bundled_library_grounds_every_clause/test_missing_mandatory_clause_fails_closed/test_partial_library_fails_closed/test_spec_appended_with_grounded_clause_text`, `test_generation_nodes::test_bundled_grounds_and_projects_kb_version/test_ungrounded_override_fails_closed`) |
| BL-08 | Compliance check + requirement normalization (normal / boundary) | complete / missing-field / vague requirements; unknown jurisdiction | high-flags on gaps; vague flagged; unknown jurisdiction surfaced not swallowed | Pass (`test_domain_services::test_complete_requirements_no_high_flags/test_missing_fields_flagged/test_vague_requirement_flagged/test_unknown_jurisdiction_surfaced_not_swallowed/test_empty_jurisdiction_zero_config_not_flagged`) |
| BL-09 | Reviewer checklist — 国家賠償法 mitigation (human-review) | compliance flags + spec | every item marked human-validation-required; flags become checklist items; standard legal items always present | Pass (`test_domain_services::test_every_item_human_validation_required/test_compliance_flags_become_checklist_items/test_standard_legal_items_always_present`) |
| BL-10 | S-2 screen — document vs field (invalid-input / adversarial) | NUL byte / control-character flood / non-text type (whole body); control chars in `procurement_objective`, `procurement_type`, `law_jurisdiction`, `timeline`, `technical_requirements`; oversized; deep nesting | the whole-body screen **refuses** (`status: error`) and `execute()` never runs; a field-level character contract and the size ceiling are **answered**, and the oversized body is never parsed | Pass (`test_input_validate_node::TestS2Gate::test_s2_answers_oversized_without_parsing_it/test_s2_answer_does_not_let_a_body_be_parsed_afterwards/test_s2_rejects_nul_byte/test_s2_does_not_coerce_binary`, `::TestRejections`, `::TestObjectiveBound`, `::TestOptionalContract`, `test_pb_input_gate::TestS2InputGateBoundaryRejection`, `test_pb_workflow::TestACorrectableRequestIsAnsweredEndToEnd::test_a_refusal_this_agent_makes_still_fails_closed`) |
| BL-11 | Field type contracts (invalid-input) | non-str `procurement_type` / `law_jurisdiction` / `timeline`; dict/non-str-item `technical_requirements`; multiline string | rejected per docs type contract; legitimate multiline strings accepted | Pass (`test_input_validate_node::test_procurement_type_non_str_not_coerced/test_law_jurisdiction_non_str_not_coerced/test_timeline_non_string_rejected/test_technical_requirements_dict_rejected/test_technical_requirements_list_non_str_item_rejected/test_technical_requirements_multiline_string_ok/test_procurement_objective_multiline_ok`) |
| BL-12 | Legitimate vendor/person name not over-blocked (boundary) | requirement text containing a vendor/person name | S-2 does not false-positive-block | Pass (`test_input_validate_node::test_legitimate_vendor_name_not_blocked`) |
| BL-13 | LLM boundary — structural isolation (non-determinism) | untrusted requirement data + system task | data isolated in a separate user message; no raw body / LLM object in the node delta | Pass (`test_generation_services::test_messages_isolate_data_from_system/test_messages_isolate_data_and_carry_flags`, `test_generation_nodes::test_no_raw_body_or_llm_in_delta`) |
| BL-14 | LLM fail-closed — draft generation (error-path / non-determinism) | LLM unavailable / raises / malformed / non-object / missing fields / control chars | propagate error; schema-invalid results rejected; no partial 仕様書 | Pass (`test_generation_nodes::test_llm_unavailable_propagates_error/test_malformed_llm_propagates_error/test_llm_exception_propagates_error/test_missing_input_fails_closed`, `test_generation_services::test_non_object_rejected/test_malformed_json_rejected/test_missing_purpose_rejected/test_missing_budget_ceiling_rejected/test_control_char_in_field_rejected/test_empty_rejected`) |
| BL-15 | S-3 output gate end-to-end (security boundary) | composed draft through post_process; `advisory_only` tampered in the field the run actually carries (absent / reshaped / not `true`) | disclaimer injected; every tamper shape **blocked** (`output_gated{blocked:true}` observed, not merely a missing output); an intact posture passes (negative control); gated draft to `formatted_output`; `output_gated` S-4 once; outer invoke returns the gated draft | Pass (`test_pb_output_gate::TestS3OutputGateBoundaryProtection::test_advisory_only_tamper_blocked_at_boundary/test_the_posture_is_checked_where_it_is_actually_carried/test_an_intact_posture_is_not_blocked`, `test_generation_nodes::test_execute_writes_disclaimer_to_formatted_output/test_execute_blocks_advisory_only_tamper/test_execute_blocks_when_the_carried_posture_is_gone/test_execute_emits_output_gated_once`, `test_graph_config_wiring::test_post_process_is_the_wired_s3_output_gate/test_invoke_reaches_inner_workflow_and_gates_output`) |
| BL-16 | Audit metadata — always-on, hash-only (audit) | audit node over a grounded run | hash-only `audit_metadata` (data hash, generated_at, `kb_version` + model version, `law_jurisdiction`) produced on every invocation | Pass (`test_generation_nodes::test_bundled_grounds_and_projects_kb_version`, `test_domain_nodes::test_compliance_emits_domain_event`) |
| BL-17 | S-3 credential BLOCK end-to-end (security boundary) | credential-shaped token in the LLM-generated draft | framework `@final` S-3 baseline scan BLOCKS fail-closed → outer invoke `status: error`, `get_output()["output"] is None`, credential body not surfaced (BLOCK posture; no downstream redaction-to-success) | Pass (`test_pb_workflow::TestS3CredentialBlockEndToEnd::test_credential_in_generated_draft_blocks_with_no_output`) |

> **Future scope (not tested in V1):** a dedicated `kokka_baisho_disclaimer` line, `docx` output, per-type
> `specification_format_templates`, and an external audit-log sink are reserved (see `docs/02` DDR). The
> substantive 国家賠償法 risk mitigation is covered by BL-03 (always-on advisory disclaimer) + BL-09
> (reviewer checklist); the audit-trail requirement by BL-16.

## Additional Regression Coverage

Guards added alongside the changes in this revision. Each entry names the test and the
condition it accepts on; they supplement the mandatory TC / PB items above and replace none
of them.

| Concern | Test | Acceptance condition |
|---|---|---|
| Deployment startup contract | `tests/unit/test_entry_point_import.py` | `src/api/server.py` — the module `uvicorn src.api.server:app` loads — imports successfully and exposes `app` together with a compiled `agent` whose `config` is a mapping. A module-level failure there stops the deployment from starting while node- and service-level tests still pass, and no other test in the suite imports that module. |
| Error → terminal routing | `tests/unit/test_inner_error_short_circuit.py` | Every non-terminal hop of the inner workflow declares a conditional branch; an errored State selects the terminal branch (`END`), a healthy State advances to the next node, and no unconditional edge remains between two domain nodes. A node failure therefore schedules no downstream node and does not amplify the `error_log` reducer channel. |
| Provisional Stage ⑤ bounded fake | `tests/unit/test_stage5_bounded_fake.py` | The fake provider is selected only by the job-scoped CI opt-in; nothing in the shipped runtime config can reach it. The real node and its validators run against the fake's output, and a failing provider still surfaces as `status: error` with no partial result. The resulting evidence proves the deploy → invoke → workflow contract and explicitly does not prove that a live provider call succeeds. |


## Test Execution Summary
- Execution date: 2026-07-20
- Total tests: 154 (`tests/unit` + `tests/proof_of_boundary`)
- Pass: 153 / Fail: 0 / Skip: 1 (PB-7 auto-skip stub — `hitl.enabled` not set)
- Evidence: GitLab CI `run-tests` job on the merge pipeline (all 12 jobs success); local `py_compile`
  of `src/` passes. Line-coverage is not gated in CI; every testable Acceptance Criterion above maps to
  ≥1 passing test.

## LLM Availability Matrix

Expected `status` and returned artifact for each state of the Azure OpenAI connection. The three
connection values (`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT`) are
resolved at call time from the invocation-bound `SecretProvider`. Declaring them in
`config/agent.yaml` `requires.secrets` states the deployment contract; it does not change any row
below. The specification draft is the deliverable, so an unavailable LLM is fail-closed.

| Condition | Expected `status` | Artifact returned | Result |
|-----------|-------------------|-------------------|--------|
| Connection values resolve, provider responds | `success` | Normalized requirements + specification draft | Pass (`test_generation_nodes::test_normalize_then_compliance_then_draft`, `test_stage5_bounded_fake::test_canonical_payload_succeeds_with_working_llm`) |
| A connection value cannot be resolved | `error` | No output | Pass (`test_generation_nodes::test_fails_closed_without_bound_secret`, `test_generation_nodes::test_llm_unavailable_propagates_error`) |
| Provider fails (error / timeout / malformed response) | `error` | No output | Pass (`test_generation_nodes::test_llm_exception_propagates_error`, `test_generation_nodes::test_malformed_llm_propagates_error`) |
