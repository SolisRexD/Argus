# Argus Semantic Core Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the first engine-independent semantic annotation core for Argus while keeping the existing UE CustomStencil capture flow intact.

**Architecture:** Add `argus_core` as a pure Python package at the project root. It owns semantic classes, annotation targets, render-policy normalization, backend strategy planning, and capture output validation. Existing UE scripts remain executable and only start consuming safe pure-core helpers where runtime risk is low.

**Tech Stack:** Python 3.11 stdlib dataclasses, enum, csv/json helpers, unittest/pytest-compatible tests, existing UE Python scripts.

---

## File Structure

- Create: `argus_core/__init__.py`  
  Public package marker and version.
- Create: `argus_core/model/__init__.py`  
  Re-export core model types.
- Create: `argus_core/model/semantics.py`  
  Defines `ColorRGB`, `SemanticClassKind`, `SemanticClass`, and helpers for class rows.
- Create: `argus_core/model/annotation.py`  
  Defines `TargetType`, `RenderPolicy`, `AnnotationTarget`, `AnnotationRule`, and legacy CSV normalization.
- Create: `argus_core/planning/__init__.py`  
  Re-export planning types.
- Create: `argus_core/planning/strategies.py`  
  Defines backend capabilities and strategy selection.
- Create: `argus_core/capture/__init__.py`  
  Re-export capture output helpers.
- Create: `argus_core/capture/outputs.py`  
  Shared stream name, file map, file existence, and output validation helpers.
- Create: `tests/test_core_annotation.py`  
  Tests target/rule/render-policy normalization.
- Create: `tests/test_core_strategies.py`  
  Tests UE-style capability strategy selection.
- Create: `tests/test_capture_outputs.py`  
  Tests shared output validation behavior.
- Modify: `scripts/capture_rgb_and_mask.py`  
  Add project root to `sys.path`; replace duplicate output helper functions with `argus_core.capture.outputs`.
- Modify: `scripts/batch_capture.py`  
  Add project root to `sys.path`; replace duplicate output helper functions with `argus_core.capture.outputs`.

---

### Task 1: Core Annotation Model

**Files:**
- Create: `argus_core/__init__.py`
- Create: `argus_core/model/__init__.py`
- Create: `argus_core/model/semantics.py`
- Create: `argus_core/model/annotation.py`
- Test: `tests/test_core_annotation.py`

- [ ] **Step 1: Write failing tests**

```python
from argus_core.model import AnnotationRule, RenderPolicy, TargetType


def test_legacy_booleans_normalize_to_visible_labeled_policy():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Road_01",
            "component_name": "StaticMeshComponent0",
            "semantic_class": "road",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "2",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.target.target_type == TargetType.COMPONENT
    assert rule.render_policy == RenderPolicy.VISIBLE_LABELED
    assert rule.effective_stencil == 2


def test_material_slot_target_is_detected_from_slot_filter():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Building_01",
            "component_name": "StaticMeshComponent0",
            "material_slot": "Window",
            "semantic_class": "glass",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "12",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.target.target_type == TargetType.MATERIAL_SLOT
    assert rule.target.material_slot == "Window"


def test_missing_mask_stencil_uses_unknown_stencil():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Unknown_01",
            "component_name": "StaticMeshComponent0",
            "semantic_class": "unknown",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.effective_stencil == 250


def test_ignore_class_uses_ignore_stencil_when_labeled():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Ignore_01",
            "component_name": "StaticMeshComponent0",
            "semantic_class": "ignore",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "99",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.effective_stencil == 254
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core_annotation.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'argus_core'`.

- [ ] **Step 3: Implement model code**

Create enums and dataclasses with `from_legacy_row()` normalization. The implementation must not import `unreal`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core_annotation.py -q`

Expected: PASS.

---

### Task 2: Backend Strategy Planning

**Files:**
- Create: `argus_core/planning/__init__.py`
- Create: `argus_core/planning/strategies.py`
- Test: `tests/test_core_strategies.py`

- [ ] **Step 1: Write failing tests**

```python
from argus_core.model import AnnotationRule
from argus_core.planning import BackendCapabilities, StrategyKind, choose_strategy


def _rule(row):
    base = {
        "actor_name": "A",
        "component_name": "C",
        "semantic_class": "road",
        "render_main_pass": "true",
        "render_custom_depth": "true",
        "stencil": "2",
    }
    base.update(row)
    return AnnotationRule.from_legacy_row(base, unknown_stencil=250, ignore_stencil=254)


def test_component_target_uses_ue_component_stencil_when_supported():
    caps = BackendCapabilities.ue_default()
    decision = choose_strategy(_rule({}), caps)

    assert decision.kind == StrategyKind.UE_COMPONENT_STENCIL
    assert decision.executable is True


def test_material_slot_target_requires_material_split_for_current_ue_backend():
    caps = BackendCapabilities.ue_default()
    decision = choose_strategy(_rule({"material_slot": "Glass"}), caps)

    assert decision.kind == StrategyKind.UE_REQUIRES_MATERIAL_SPLIT
    assert decision.executable is False
    assert "material slot" in decision.reason.lower()


def test_instance_target_requires_instance_split_for_current_ue_backend():
    caps = BackendCapabilities.ue_default()
    decision = choose_strategy(_rule({"instance_index": "3"}), caps)

    assert decision.kind == StrategyKind.UE_REQUIRES_INSTANCE_SPLIT
    assert decision.executable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core_strategies.py -q`

Expected: FAIL because planning module does not exist.

- [ ] **Step 3: Implement strategy planning**

Implement `BackendCapabilities`, `StrategyKind`, `StrategyDecision`, and `choose_strategy()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core_strategies.py -q`

Expected: PASS.

---

### Task 3: Shared Capture Output Validation

**Files:**
- Create: `argus_core/capture/__init__.py`
- Create: `argus_core/capture/outputs.py`
- Test: `tests/test_capture_outputs.py`

- [ ] **Step 1: Write failing tests**

```python
import json

from argus_core.capture import (
    check_required_stream_files,
    expected_stream_names,
    extract_stream_file_map,
)


def test_expected_stream_names_uses_configured_unique_streams():
    cfg = {
        "capture": {
            "streams": [
                {"name": "rgb"},
                {"name": "mask"},
                {"name": "mask"},
                {"name": "depth"},
            ]
        }
    }

    assert expected_stream_names(cfg) == ["rgb", "mask", "depth"]


def test_extract_stream_file_map_prefers_files_json():
    row = {
        "files_json": json.dumps({"rgb": "from_json_rgb.png"}),
        "rgb_file": "legacy_rgb.png",
        "mask_file": "legacy_mask.png",
    }

    assert extract_stream_file_map(row) == {
        "rgb": "from_json_rgb.png",
        "mask": "legacy_mask.png",
    }


def test_check_required_stream_files_reports_missing_and_bad_paths(tmp_path):
    good = tmp_path / "rgb.png"
    good.write_bytes(b"x")

    ok, missing, bad_paths = check_required_stream_files(
        {"rgb": str(good), "mask": str(tmp_path / "missing.png")},
        ["rgb", "mask", "depth"],
    )

    assert ok is False
    assert missing == ["depth"]
    assert bad_paths == {"mask": str(tmp_path / "missing.png")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_capture_outputs.py -q`

Expected: FAIL because capture output module does not exist.

- [ ] **Step 3: Implement shared output helpers**

Move the duplicated stream/file-map logic from `capture_rgb_and_mask.py` and `batch_capture.py` into pure core helpers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_capture_outputs.py -q`

Expected: PASS.

---

### Task 4: Integrate Shared Output Helpers Into Existing Scripts

**Files:**
- Modify: `scripts/capture_rgb_and_mask.py`
- Modify: `scripts/batch_capture.py`

- [ ] **Step 1: Update imports and path bootstrap**

Add project root to `sys.path` in both scripts:

```python
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

for path in [PROJECT_ROOT, SCRIPT_DIR]:
    if path not in sys.path:
        sys.path.insert(0, path)
```

Import:

```python
from argus_core.capture import (
    check_required_stream_files,
    expected_stream_names,
    extract_stream_file_map,
)
```

- [ ] **Step 2: Replace duplicate helper functions**

Replace local `_get_expected_stream_names()` calls with `expected_stream_names()`.

Replace local `_extract_stream_file_map_from_row()` calls with `extract_stream_file_map()`.

Replace direct stream file checks with `check_required_stream_files()`.

- [ ] **Step 3: Compile affected scripts**

Run: `python -m py_compile scripts/capture_rgb_and_mask.py scripts/batch_capture.py`

Expected: exit code 0.

---

### Task 5: Full Verification

**Files:**
- All new and changed Python files.

- [ ] **Step 1: Run pure Python tests**

Run: `python -m pytest tests -q`

Expected: all tests pass.

- [ ] **Step 2: Compile all scripts**

Run: `Get-ChildItem scripts -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '\\__pycache__\\' } | ForEach-Object { python -m py_compile $_.FullName }`

Expected: exit code 0.

- [ ] **Step 3: Verify core has no UE dependency**

Run: `python -c "import argus_core; import argus_core.model; import argus_core.planning; import argus_core.capture; print('argus_core import ok')"`

Expected: `argus_core import ok`.

---
