# Argus UE Backend Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate Argus pure-Python configuration and CSV services from Unreal Engine APIs while preserving every existing UE script import.

**Architecture:** Add `argus_core.io` for engine-independent paths, configuration, primitive parsing, semantic CSV parsing, and pose CSV parsing. Add `argus_backends.ue` for Unreal logging, world, actor, asset, and scene-capture helpers. Keep `scripts/common.py` as a compatibility facade, and make `argus_components` exports lazy so data-only services can import without an Unreal runtime.

**Tech Stack:** Python 3.11 standard library, Unreal Engine 5.8 Python API, pytest.

---

### Task 1: Pure-Python IO Boundary

**Files:**
- Create: `argus_core/io/__init__.py`
- Create: `argus_core/io/paths.py`
- Create: `argus_core/io/parsing.py`
- Create: `argus_core/io/config.py`
- Create: `argus_core/io/csv_data.py`
- Create: `tests/test_core_io.py`

- [x] **Step 1: Write failing tests**

Add tests that import `argus_core.io` without an `unreal` module and verify boolean/integer/float parsing, relative path resolution, semantic class CSV normalization, annotation CSV preservation, and pose defaults.

- [x] **Step 2: Verify RED**

Run: `python -m pytest tests/test_core_io.py -q`

Expected: collection fails because `argus_core.io` does not exist.

- [x] **Step 3: Implement the minimal IO package**

Move the existing pure functions without changing their return shapes. `load_json_config()` accepts an optional `project_root` so callers are not tied to the legacy `scripts/` layout.

- [x] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_core_io.py -q`

Expected: all tests pass.

### Task 2: Explicit UE Backend

**Files:**
- Create: `argus_backends/__init__.py`
- Create: `argus_backends/ue/__init__.py`
- Create: `argus_backends/ue/editor.py`
- Modify: `scripts/common.py`
- Test: existing UE helper tests

- [x] **Step 1: Add a compatibility test**

Extend the import-boundary tests to install a minimal fake `unreal` module, import `common`, and assert that all legacy public and private helper names remain available.

- [x] **Step 2: Verify RED**

Run the focused test and confirm it fails because `argus_backends.ue` is absent.

- [x] **Step 3: Move UE-only helpers**

Move logging, rotator, world lookup, actor lookup, asset loading, and `SceneCaptureSource` selection into `argus_backends.ue.editor`. Replace `scripts/common.py` with explicit re-exports from `argus_core.io` and `argus_backends.ue.editor`.

- [x] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_ue_actor_lookup.py tests/test_post_process_builder.py tests/test_runtime_capture_controller.py -q`

Expected: all focused compatibility tests pass.

### Task 3: Data Pipeline Import Independence

**Files:**
- Modify: `scripts/argus_components/data_pipeline.py`
- Modify: `scripts/argus_components/__init__.py`
- Create: `tests/test_import_boundaries.py`

- [x] **Step 1: Write a subprocess import test**

Start a normal Python interpreter with project root and `scripts/` on `PYTHONPATH`, then import `argus_core`, `argus_core.io`, and `argus_components.data_pipeline` while asserting `unreal` was never imported.

- [x] **Step 2: Verify RED**

Run: `python -m pytest tests/test_import_boundaries.py -q`

Expected: `argus_components.data_pipeline` fails because package initialization eagerly imports UE services.

- [x] **Step 3: Implement lazy component exports**

Use a static export map plus module `__getattr__()` in `argus_components.__init__`. Import IO helpers directly from `argus_core.io` in `DataPipelineService`.

- [x] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_import_boundaries.py -q`

Expected: subprocess exits successfully and reports that `unreal` is absent.

### Task 4: Full Verification and Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/workflow.md`

- [x] **Step 1: Document the new ownership boundary**

Update the directory tree and state that `scripts/common.py` is compatibility-only. Document direct imports for core IO and UE backend helpers.

- [x] **Step 2: Run full verification**

Run: `python -m pytest -q`

Expected: all tests pass.

Run: `python -m compileall -q argus_core argus_backends scripts`

Expected: exit code 0.

- [x] **Step 3: Review repository state and commit**

Review `git diff --check`, ensure untracked `AGENTS.md` is not staged, and commit only this iteration's files.
