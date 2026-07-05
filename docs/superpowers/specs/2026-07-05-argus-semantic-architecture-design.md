# Argus Semantic Architecture Refactor Design

## Goal

Argus will move from a UE script prototype into an engine-independent semantic annotation system. The core system defines what should be annotated, validated, planned, and captured. Engine adapters decide how a target engine implements that plan.

The first backend remains Unreal Engine. Its first concrete strategy is still CustomDepth / CustomStencil plus a post-process material, because that is already working and uses UE efficiently. The refactor must not treat that UE mechanism as the whole product model.

## Current System Summary

The current code gets semantic masks with this UE-specific flow:

1. `semantic_map.csv` describes `actor_name`, `component_name`, `semantic_class`, `render_main_pass`, `render_custom_depth`, and `stencil`.
2. `writeback_semantic_stencil.py` reads those rules and resolves each row to a UE component.
3. `AnnotationController` writes:
   - `render_in_main_pass`
   - `render_custom_depth`
   - `custom_depth_stencil_value`
4. `build_semantic_pp_material.py` builds a post-process material that reads `SceneTexture: CustomStencil`.
5. The generated HLSL maps stencil IDs to either class colors or grayscale IDs.
6. `setup_dual_capture.py` configures RGB and mask `SceneCapture2D` actors.
7. `CaptureService` captures the streams, exports render targets, and writes metadata.

This is a valid component-level UE semantic mask pipeline. Its main limitation is that `custom_depth_stencil_value` is a component-level property. Current CSV fields such as `material_slot` and `instance_index` help identify a component, but they do not yet create true material-slot-level or instance-level pixel labels.

## Target Architecture

The refactor splits Argus into four layers:

```text
argus_core/
  model/
  planning/
  validation/
  io/

argus_backends/
  ue/
  omniverse/

argus_apps/
  ue_editor/
  cli/

scripts/
  thin wrappers or legacy compatibility only
```

### `argus_core`

Pure Python. It must not import `unreal`.

Responsibilities:

- Define semantic classes.
- Define annotation targets.
- Define annotation rules.
- Build annotation plans.
- Build capture plans.
- Validate rule conflicts, missing classes, output schema, backend capability mismatches, and unsupported target granularities.
- Read and write stable CSV / JSON schemas.

### `argus_backends.ue`

UE-specific implementation. This layer may import `unreal`.

Responsibilities:

- Scan UE scene objects and convert them into core inventory records.
- Resolve core annotation targets to UE objects.
- Build or update UE render targets.
- Build or update UE post-process materials.
- Apply annotation plans through UE implementation strategies.
- Configure and run `SceneCapture2D`.
- Export render targets.

### `argus_backends.omniverse`

Future adapter target. The first refactor only reserves the interface shape. It should not be implemented now unless needed.

### `argus_apps`

Thin orchestration layer.

Responsibilities:

- Load config.
- Call core planning and validation.
- Call selected backend.
- Print logs.
- Return structured results.

UE editor scripts should become wrappers over `argus_apps.ue_editor`, not business logic containers.

## Core Domain Model

The first core model should support mixed annotation granularity even if not every backend strategy can implement every target immediately.

### `SemanticClass`

Fields:

- `name`
- `stencil`
- `color_rgb`
- `kind`: `normal`, `background`, `unknown`, `ignore`

Rules:

- `background` conventionally uses stencil `0`.
- `unknown` defaults to stencil `250`.
- `ignore` defaults to stencil `254`.
- Duplicate stencil values are warnings or errors depending on config, because a post-process stencil map can only produce one color per stencil value.

### `AnnotationTarget`

Fields:

- `target_type`: `component`, `material_slot`, `instance`, `proxy`
- `actor_name`
- `component_name`
- optional `mesh_name`
- optional `mesh_path`
- optional `material_name`
- optional `material_path`
- optional `material_slot`
- optional `instance_index`
- optional `proxy_id`

Semantics:

- `component`: one UE primitive component receives one label.
- `material_slot`: a sub-region of a component receives a label based on material slot.
- `instance`: one instanced mesh element receives a label.
- `proxy`: a dedicated proxy object carries annotation for another source object.

### `AnnotationRule`

Fields:

- `target`
- `semantic_class`
- `render_policy`
- optional `stencil_override`
- optional review fields: `confidence`, `reason`, `review_status`, `notes`

`render_policy` replaces the current pair of loose booleans with a named model:

- `visible_labeled`: RGB visible and mask labeled.
- `visible_unlabeled`: RGB visible and mask ignored.
- `hidden_labeled`: RGB hidden and mask labeled, usually proxy-only.
- `hidden_unlabeled`: RGB hidden and mask ignored.

The CSV may still expose booleans for compatibility, but the core model should normalize them into this enum.

### `AnnotationPlan`

Contains normalized rules plus strategy decisions. It answers:

- Which targets should be labeled?
- Which semantic class and stencil should each target use?
- Which backend strategy should be used?
- Which rules are implementable now?
- Which rules need proxy, mesh split, material pass, or future backend support?

## Backend Strategy Model

Backend adapters should report capabilities before execution.

Example UE capabilities:

```text
component_custom_stencil: supported
material_slot_custom_stencil: unsupported_direct
instance_custom_stencil: unsupported_direct
proxy_custom_stencil: supported_if_proxy_exists
scene_capture_multistream: supported
custom_stencil_post_process: supported
```

The planner maps each rule to an implementation strategy:

- `ue_component_stencil`: write `render_custom_depth` and `custom_depth_stencil_value` on a component.
- `ue_proxy_stencil`: write stencil to an explicit proxy component.
- `ue_requires_material_split`: material slot target requires mesh/material split before stencil can be accurate.
- `ue_requires_instance_split`: instance target requires instance extraction, proxy instances, or another engine-specific path.
- `unsupported`: the backend cannot implement this target.

First-stage UE implementation should execute only strategies that are safe and already supported:

- `ue_component_stencil`
- `ue_proxy_stencil` when the proxy is represented by a normal primitive component

Material-slot and instance targets should be accepted by the model and validation, but reported as not directly implementable by the current UE stencil strategy unless a proxy or split strategy is explicitly provided.

## Data Flow

### Inventory

UE backend scans the scene and outputs core inventory records:

```text
SceneObject
  actor
  component
  mesh
  material slots
  instance count
  backend refs only inside backend memory, never in persisted core JSON
```

Persisted inventory must stay backend-neutral where possible, with a `backend` field when needed.

### Annotation Planning

Core reads:

- semantic class table
- annotation rules
- scene inventory
- backend capability profile

Core outputs:

- validation report
- annotation plan
- backend execution plan

### UE Execution

UE backend reads the execution plan and applies only supported strategies.

For `ue_component_stencil`, it writes:

- `render_in_main_pass`
- `render_custom_depth`
- `custom_depth_stencil_value`

For the mask stream, it keeps the current efficient UE mechanism:

- post-process material domain
- `SceneTextureId.PPI_CUSTOM_STENCIL`
- generated stencil-to-color HLSL
- `SceneCapture2D` mask stream with the post-process material

### Capture

Core owns the capture plan:

- stream names
- required outputs
- camera pose rows
- metadata schema
- resume policy

UE backend owns execution:

- actor lookup
- pose application
- intrinsics application
- render target export

## Error Handling

Validation must classify problems before backend execution:

- `error`: unsafe to execute, such as missing class, ambiguous component target, duplicate exact target with conflicting class, invalid render policy.
- `warning`: executable but risky, such as translucent material without `Allow Custom Depth Writes`, duplicate stencil color mapping, unsupported target strategy deferred.
- `info`: normalized behavior, such as missing stencil resolved to `unknown`.

Backend execution should not silently degrade target granularity. If a `material_slot` rule cannot become a true per-slot mask, it must be reported as `requires_material_split` or `unsupported`, not applied to the whole component.

## Migration Plan

The refactor should be staged.

### Stage 1: Core Extraction

- Add `argus_core` package.
- Move parse/normalize logic out of `common.py`.
- Add pure Python models for semantic classes, targets, rules, render policies, stream specs, poses, and validation results.
- Add tests for rule normalization and backend capability planning.

### Stage 2: UE Adapter Boundary

- Add `argus_backends.ue`.
- Move UE calls out of `common.py` and existing component modules into UE adapter modules.
- Keep the current CustomStencil flow as `ue_component_stencil`.
- Keep current scripts as wrappers.

### Stage 3: Planning and Validation

- Replace direct CSV-to-writeback flow with:
  `CSV -> core model -> validation -> annotation plan -> UE execution plan -> backend apply`.
- Add capability report columns to validation CSV.
- Mark material-slot and instance rules as explicitly deferred unless a supported strategy is present.

### Stage 4: Capture Separation

- Split `capture_system.py` into stream registry, camera intrinsics, UE scene capture setup, UE capture execution, and output validation.
- Move duplicate single-frame and batch output validation into core or app-level shared services.

### Stage 5: Future Strategies

Add strategy implementations only after the model and reports are stable:

- proxy-based annotation
- mesh/material split workflow
- instance extraction or proxy instance workflow
- Omniverse backend mapping

## Testing Strategy

Without UE editor access, first-stage verification should focus on pure Python behavior:

- semantic class parsing
- render policy normalization
- target granularity parsing
- capability-based strategy selection
- validation severity classification
- output file map extraction
- metadata resume planning

UE-specific files can be checked with `py_compile`, but runtime behavior must be verified inside UE after editor access is available.

## Non-Goals For First Implementation

- Do not build a UE plugin UI yet.
- Do not implement Omniverse runtime behavior yet.
- Do not pretend material-slot or instance-level labels work through component stencil.
- Do not replace the working CustomStencil mask flow.
- Do not require UE editor access for pure model tests.

## Acceptance Criteria

- `argus_core` imports successfully in normal Python without `unreal`.
- Existing UE scripts can still be called as wrappers, or replacement wrappers are documented.
- Component-level semantic mask flow remains represented as a UE backend strategy.
- Material-slot and instance-level targets can be parsed and validated.
- Unsupported target granularities produce explicit validation results.
- `capture_system.py` no longer remains the only home for stream config, camera intrinsics, capture execution, and output validation.
- Pure Python tests cover the new core model and planner.
