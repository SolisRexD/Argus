# Argus Semantic Planner Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route production semantic validation and writeback through the engine-independent annotation model and capability planner without silently degrading unsupported target granularity.

**Architecture:** Make `argus_core.planning` reason only about generic target capabilities. Advertise UE 5.8 CustomStencil support from `argus_backends.ue.semantics`. Keep `SemanticRuleBuilder` as a compatibility adapter that converts legacy CSV rows into a core `AnnotationRule`, a generic `StrategyDecision`, and the existing UE execution context.

**Tech Stack:** Python 3.11 dataclasses/enums, UE 5.8 Python component properties, pytest.

---

### Task 1: Generic Capability Planner

**Files:**
- Modify: `argus_core/planning/strategies.py`
- Modify: `tests/test_core_strategies.py`

- [x] Write tests proving strategy selection is independent of backend name and supports direct component, material-slot, instance, and proxy capability combinations.
- [x] Run the focused tests and confirm they fail against UE-prefixed strategy names and `ue_default()`.
- [x] Replace UE-prefixed capabilities and strategies with generic labeling capabilities and decisions.
- [x] Run focused tests and confirm they pass.

### Task 2: UE Semantic Capability Adapter

**Files:**
- Create: `argus_backends/ue/semantics.py`
- Modify: `argus_backends/ue/__init__.py`
- Create: `tests/test_ue_semantic_adapter.py`

- [x] Write a normal-Python test that imports UE semantic capabilities without importing `unreal`.
- [x] Confirm the test fails because the adapter is absent and the UE package eagerly imports editor APIs.
- [x] Add lazy UE package exports and a `default_annotation_capabilities()` factory describing component/proxy support and deferred material-slot/instance support.
- [x] Confirm the adapter test passes.

### Task 3: Production Rule Adapter

**Files:**
- Modify: `scripts/argus_components/annotation_control.py`
- Create: `tests/test_annotation_rule_adapter.py`

- [x] Write tests proving `SemanticRuleBuilder` retains a core `AnnotationRule`, exposes the strategy decision, and derives legacy execution fields from the normalized model.
- [x] Confirm tests fail because production parsing is duplicated.
- [x] Delegate parsing and strategy selection to `AnnotationRule.from_legacy_row()` and `choose_strategy()`.
- [x] Confirm builder tests and existing annotation tests pass.

### Task 4: Validation and Writeback Enforcement

**Files:**
- Modify: `scripts/argus_components/annotation_control.py`
- Modify: `scripts/validate_semantic_map.py`
- Modify: `scripts/argus_components/data_pipeline.py`
- Modify: `tests/test_annotation_rule_adapter.py`

- [x] Write a failing test proving an unsupported material-slot rule does not mutate a UE component.
- [x] Add strategy fields to validation/writeback output and return deferred strategy status before component mutation.
- [x] Use normalized target fields for component resolution instead of the original CSV row.
- [x] Run focused tests and confirm they pass.

### Task 5: Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/workflow.md`

- [x] Document that material-slot and instance rules are modeled but deferred by the current UE CustomStencil backend.
- [x] Run `python -m pytest -q`, `python -m compileall -q argus_core argus_backends scripts`, and `git diff --check`.
- [x] Run a UE editor Python import smoke test through the MCP Output Log console.
- [x] Commit the verified checkpoint without staging `AGENTS.md`.
