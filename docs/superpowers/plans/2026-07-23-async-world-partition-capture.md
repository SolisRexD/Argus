# Async World Partition Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first Argus capture in a fresh CitySample PIE session wait for World Partition, apply semantics after streaming, and export correct results without blocking UE Tick.

**Architecture:** `CaptureService.capture_once()` prepares the scene synchronously, then returns a `CaptureJob` driven by UE 5.8 Slate post-tick callbacks. The job waits for stable World Partition completion, performs non-blocking warm-up, writes semantic stencils, captures on the following Tick, and always restores runtime state. Single-frame and batch entrypoints consume the same job API; batch execution remains strictly serial.

**Tech Stack:** Python 3, Unreal Engine 5.8 Python API, Slate post-tick callbacks, World Partition subsystem, pytest.

---

## File map

- `argus_core/capture/runtime.py`: backend-neutral runtime plan, including the streaming timeout.
- `config/pipeline_config.json`: CitySample timeout calibration.
- `scripts/argus_components/runtime_control.py`: immediate runtime preparation and World Partition readiness query.
- `scripts/argus_components/capture_system.py`: `CaptureJob` and the asynchronous capture pipeline.
- `scripts/capture_rgb_and_mask.py`: asynchronous single-capture finalizer.
- `scripts/capture_pose_probe.py`: prints probe output after job completion.
- `scripts/batch_capture.py`: serial asynchronous batch runner.
- `tests/test_capture_runtime.py`: pure runtime-plan coverage.
- `tests/test_runtime_capture_controller.py`: non-blocking controller and subsystem query coverage.
- `tests/test_capture_async_job.py`: state-machine ordering, timeout, and cleanup coverage.
- `tests/test_async_batch_capture.py`: batch serialization coverage.
- `tests/test_capture_stream_post_process.py`: existing native blendable regression coverage.

### Task 1: Preserve the verified native blendable fix

**Files:**
- Modify: `scripts/argus_components/capture_system.py:532`
- Test: `tests/test_capture_stream_post_process.py`

- [ ] **Step 1: Run the focused regression test**

Run:

```powershell
python -m pytest tests/test_capture_stream_post_process.py -q
```

Expected: `3 passed`.

- [ ] **Step 2: Confirm the implementation uses the UE 5.8 native API**

The method must remain:

```python
def _set_post_process_material(self, scene_capture_comp, material):
    """Set one full-weight post-process material on a capture component."""
    scene_capture_comp.add_or_update_blendable(material, 1.0)
```

- [ ] **Step 3: Commit only the verified blendable files**

```powershell
git add -- scripts/argus_components/capture_system.py tests/test_capture_stream_post_process.py
git commit -m "Fix semantic capture blendable binding"
```

Do not add `AGENTS.md`.

### Task 2: Add a configurable streaming timeout

**Files:**
- Modify: `argus_core/capture/runtime.py`
- Modify: `config/pipeline_config.json`
- Test: `tests/test_capture_runtime.py`

- [ ] **Step 1: Write failing timeout assertions**

Add to `test_runtime_preparation_is_disabled_by_default()`:

```python
assert plan.streaming_timeout_seconds == 120.0
```

Add to `test_citysample_bigcity_profile_generates_world_partition_commands()` input:

```python
"streaming_timeout_seconds": 45.0,
```

and assertion:

```python
assert plan.streaming_timeout_seconds == 45.0
```

- [ ] **Step 2: Run the focused test and verify failure**

```powershell
python -m pytest tests/test_capture_runtime.py -q
```

Expected: FAIL because `RuntimePreparationPlan` has no `streaming_timeout_seconds` field.

- [ ] **Step 3: Add the timeout to the runtime plan**

In `RuntimePreparationPlan`, place the field after `wait_for_streaming`:

```python
streaming_timeout_seconds: float = 120.0
```

Add it to `to_metadata()`:

```python
"streaming_timeout_seconds": self.streaming_timeout_seconds,
```

Add it to `build_runtime_preparation_plan()`:

```python
streaming_timeout_seconds=max(
    0.0,
    _parse_float(runtime_cfg.get("streaming_timeout_seconds"), 120.0),
),
```

Add this property beside `wait_for_streaming` in `config/pipeline_config.json`:

```json
"streaming_timeout_seconds": 120.0,
```

- [ ] **Step 4: Run the focused test and verify success**

```powershell
python -m pytest tests/test_capture_runtime.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- argus_core/capture/runtime.py config/pipeline_config.json tests/test_capture_runtime.py
git commit -m "Add capture streaming timeout"
```

### Task 3: Make runtime preparation non-blocking

**Files:**
- Modify: `scripts/argus_components/runtime_control.py`
- Test: `tests/test_runtime_capture_controller.py`

- [ ] **Step 1: Add tests for immediate preparation and readiness querying**

Append these tests:

```python
def test_prepare_for_capture_requests_streaming_without_sleeping_or_pausing(monkeypatch):
    module = import_runtime_control(monkeypatch)
    events = []
    world = object()
    controller = module.RuntimeCaptureController()
    controller._get_world = lambda: world
    controller._execute_console_command = lambda current_world, command: events.append(
        ("console", current_world, command)
    )
    controller._move_player_streaming_source = lambda *args, **kwargs: events.append(
        ("move", args[0])
    )
    controller._flush_level_streaming = lambda current_world: events.append(
        ("flush", current_world)
    )
    controller.set_game_paused = lambda paused, world=None: events.append(
        ("paused", paused)
    )

    cfg = {
        "runtime": {
            "enabled": True,
            "profile": "generic",
            "warmup_seconds": 5.0,
            "pause_after_warmup": True,
            "wait_for_streaming": True,
            "move_player_to_capture": True,
        }
    }
    pose = {"x": 1, "y": 2, "z": 3, "pitch": 0, "yaw": 0, "roll": 0}

    plan = controller.prepare_for_capture(cfg, pose=pose, capture_actor=object())

    assert plan.warmup_seconds == 5.0
    assert ("flush", world) in events
    assert not any(event[0] == "paused" for event in events)


def test_is_streaming_completed_uses_world_partition_subsystem(monkeypatch):
    module = import_runtime_control(monkeypatch)
    world = object()
    subsystem = types.SimpleNamespace(is_all_streaming_completed=lambda: True)
    module.unreal.WorldPartitionSubsystem = object()
    module.unreal.SubsystemBlueprintLibrary = types.SimpleNamespace(
        get_world_subsystem=lambda context, cls: subsystem
    )
    controller = module.RuntimeCaptureController()
    controller._get_world = lambda: world

    assert controller.is_streaming_completed() is True


def test_is_streaming_completed_rejects_missing_subsystem(monkeypatch):
    module = import_runtime_control(monkeypatch)
    module.unreal.WorldPartitionSubsystem = object()
    module.unreal.SubsystemBlueprintLibrary = types.SimpleNamespace(
        get_world_subsystem=lambda context, cls: None
    )
    controller = module.RuntimeCaptureController()
    controller._get_world = lambda: object()

    with pytest.raises(RuntimeError, match="World Partition subsystem"):
        controller.is_streaming_completed()
```

Add `import pytest` at the top of the test file.

- [ ] **Step 2: Run tests and verify the non-blocking expectation fails**

```powershell
python -m pytest tests/test_runtime_capture_controller.py -q
```

Expected: FAIL because preparation still sleeps/pauses and readiness query does not exist.

- [ ] **Step 3: Remove synchronous waiting from the controller**

Delete `import time`, remove the `sleep_fn` constructor argument, and keep only player restore state:

```python
def __init__(self):
    self._player_restore_state = None
```

Delete these branches from `prepare_for_capture()`:

```python
if plan.warmup_seconds > 0:
    self._sleep(plan.warmup_seconds)

if plan.pause_after_warmup:
    self.set_game_paused(True, world=world)
```

Add the readiness query after `set_game_paused()`:

```python
def is_streaming_completed(self, world=None):
    """Return whether the current world's World Partition work is complete."""
    world = world or self._get_world()
    if not world:
        raise RuntimeError("Unable to query World Partition streaming; no UE world is available")

    subsystem = unreal.SubsystemBlueprintLibrary.get_world_subsystem(
        world,
        unreal.WorldPartitionSubsystem,
    )
    if not subsystem:
        raise RuntimeError("Unable to query World Partition streaming; no World Partition subsystem is available")

    return bool(subsystem.is_all_streaming_completed())
```

- [ ] **Step 4: Run focused tests**

```powershell
python -m pytest tests/test_runtime_capture_controller.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- scripts/argus_components/runtime_control.py tests/test_runtime_capture_controller.py
git commit -m "Make runtime capture preparation nonblocking"
```

### Task 4: Add the Tick-driven CaptureJob

**Files:**
- Modify: `scripts/argus_components/capture_system.py`
- Create: `tests/test_capture_async_job.py`

- [ ] **Step 1: Write the state-machine tests**

Create `tests/test_capture_async_job.py` with fakes that import `capture_system` using a fake `unreal` module. Cover these exact behaviors:

```python
def test_job_waits_for_two_streaming_ticks_then_warmup_and_capture(monkeypatch):
    module, unreal_api = import_capture_system(monkeypatch)
    clock = FakeClock()
    events = []
    readiness = iter([False, True, True, True])
    plan = RuntimePreparationPlan(
        enabled=True,
        warmup_seconds=2.0,
        wait_for_streaming=True,
        streaming_timeout_seconds=10.0,
    )
    job = module.CaptureJob(
        capture_id="harbor",
        runtime_plan=plan,
        is_streaming_completed=lambda: next(readiness),
        prepare_semantics=lambda: events.append("semantics") or {"scanned": 29329},
        capture=lambda stats: events.append(("capture", stats)) or {"capture_id": "harbor"},
        cleanup=lambda: events.append("cleanup"),
        clock=clock,
    ).start()

    unreal_api.tick()
    unreal_api.tick()
    unreal_api.tick()
    assert events == []

    clock.advance(2.0)
    unreal_api.tick()
    assert events == ["semantics"]
    assert job.done is False

    unreal_api.tick()
    assert job.done is True
    assert job.result == {"capture_id": "harbor"}
    assert events == [
        "semantics",
        ("capture", {"scanned": 29329}),
        "cleanup",
    ]
    assert unreal_api.unregister_count == 1


def test_job_resets_warmup_when_streaming_regresses(monkeypatch):
    module, unreal_api = import_capture_system(monkeypatch)
    clock = FakeClock()
    readiness = iter([True, True, False, True, True, True])
    events = []
    plan = RuntimePreparationPlan(
        enabled=True,
        warmup_seconds=1.0,
        wait_for_streaming=True,
        streaming_timeout_seconds=10.0,
    )
    job = module.CaptureJob(
        capture_id="retry",
        runtime_plan=plan,
        is_streaming_completed=lambda: next(readiness),
        prepare_semantics=lambda: events.append("semantics") or {},
        capture=lambda stats: {},
        cleanup=lambda: None,
        clock=clock,
    ).start()

    unreal_api.tick()
    unreal_api.tick()
    clock.advance(1.0)
    unreal_api.tick()
    assert events == []

    unreal_api.tick()
    unreal_api.tick()
    clock.advance(1.0)
    unreal_api.tick()
    assert events == ["semantics"]
    assert job.done is False


def test_job_timeout_and_capture_error_both_cleanup(monkeypatch):
    module, unreal_api = import_capture_system(monkeypatch)
    clock = FakeClock()
    cleanup_events = []
    plan = RuntimePreparationPlan(
        enabled=True,
        wait_for_streaming=True,
        streaming_timeout_seconds=1.0,
    )
    timeout_job = module.CaptureJob(
        capture_id="timeout",
        runtime_plan=plan,
        is_streaming_completed=lambda: False,
        prepare_semantics=lambda: {},
        capture=lambda stats: {},
        cleanup=lambda: cleanup_events.append("timeout"),
        clock=clock,
    ).start()
    clock.advance(2.0)
    unreal_api.tick()

    assert timeout_job.done is True
    assert isinstance(timeout_job.error, TimeoutError)
    assert cleanup_events == ["timeout"]

    error_job = module.CaptureJob(
        capture_id="error",
        runtime_plan=RuntimePreparationPlan(enabled=False),
        is_streaming_completed=lambda: True,
        prepare_semantics=lambda: {},
        capture=lambda stats: (_ for _ in ()).throw(RuntimeError("capture failed")),
        cleanup=lambda: cleanup_events.append("error"),
        clock=clock,
    ).start()
    unreal_api.tick()
    unreal_api.tick()
    unreal_api.tick()

    assert str(error_job.error) == "capture failed"
    assert cleanup_events == ["timeout", "error"]
    assert unreal_api.unregister_count == 2
```

The file must define `FakeClock`, a fake Unreal callback registry with `tick()`, and an `import_capture_system()` helper matching the existing import-isolation pattern in `tests/test_capture_stream_post_process.py`.

- [ ] **Step 2: Run the new tests and verify failure**

```powershell
python -m pytest tests/test_capture_async_job.py -q
```

Expected: FAIL because `CaptureJob` does not exist.

- [ ] **Step 3: Implement the minimal CaptureJob**

Add `CaptureJob` before `CaptureService` in `capture_system.py`. Its constructor must accept exactly the dependencies used by the tests:

```python
class CaptureJob:
    """Advance one capture between UE editor ticks."""

    def __init__(
        self,
        capture_id,
        runtime_plan,
        is_streaming_completed,
        prepare_semantics,
        capture,
        cleanup,
        clock=None,
    ):
        self.capture_id = capture_id
        self.runtime_plan = runtime_plan
        self.done = False
        self.result = None
        self.error = None
        self._is_streaming_completed = is_streaming_completed
        self._prepare_semantics = prepare_semantics
        self._capture = capture
        self._cleanup = cleanup
        self._clock = clock or time.monotonic
        self._callbacks = []
        self._tick_handle = None
        self._started_at = None
        self._warmup_started_at = None
        self._stable_streaming_ticks = 0
        self._semantic_stats = None
        self._state = "waiting"

    def start(self):
        self._started_at = self._clock()
        try:
            self._tick_handle = unreal.register_slate_post_tick_callback(self._on_tick)
        except Exception as exc:
            self._finish(error=exc)
        return self

    def add_done_callback(self, callback):
        if self.done:
            self._notify(callback)
        else:
            self._callbacks.append(callback)
        return self

    def _on_tick(self, _delta_seconds):
        if self.done:
            return
        try:
            now = self._clock()
            timeout = max(0.0, float(self.runtime_plan.streaming_timeout_seconds))
            if timeout and now - self._started_at > timeout:
                raise TimeoutError(
                    "Capture '{}' timed out after {:.1f}s waiting for streaming".format(
                        self.capture_id,
                        timeout,
                    )
                )

            if self._state == "waiting":
                self._tick_waiting(now)
            elif self._state == "warming":
                self._tick_warming(now)
            elif self._state == "capturing":
                self._finish(result=self._capture(self._semantic_stats))
        except Exception as exc:
            self._finish(error=exc)

    def _must_wait_for_streaming(self):
        return bool(self.runtime_plan.enabled and self.runtime_plan.wait_for_streaming)

    def _tick_waiting(self, now):
        if not self._must_wait_for_streaming():
            self._state = "warming"
            self._warmup_started_at = now
            return

        if self._is_streaming_completed():
            self._stable_streaming_ticks += 1
        else:
            self._stable_streaming_ticks = 0

        if self._stable_streaming_ticks >= 2:
            self._state = "warming"
            self._warmup_started_at = now

    def _tick_warming(self, now):
        if self._must_wait_for_streaming() and not self._is_streaming_completed():
            self._state = "waiting"
            self._stable_streaming_ticks = 0
            self._warmup_started_at = None
            return

        if now - self._warmup_started_at < max(0.0, float(self.runtime_plan.warmup_seconds)):
            return

        self._semantic_stats = self._prepare_semantics()
        self._state = "capturing"

    def _finish(self, result=None, error=None):
        if self.done:
            return

        if self._tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self._tick_handle)
            self._tick_handle = None

        try:
            self._cleanup()
        except Exception as cleanup_error:
            if error is None:
                error = cleanup_error

        self.result = result if error is None else None
        self.error = error
        self.done = True
        callbacks = self._callbacks
        self._callbacks = []
        for callback in callbacks:
            self._notify(callback)

    def _notify(self, callback):
        try:
            callback(self)
        except Exception as exc:
            warn("Capture completion callback failed: {}".format(exc))
```

- [ ] **Step 4: Run the state-machine tests**

```powershell
python -m pytest tests/test_capture_async_job.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- scripts/argus_components/capture_system.py tests/test_capture_async_job.py
git commit -m "Add tick driven capture job"
```

### Task 5: Route CaptureService through CaptureJob

**Files:**
- Modify: `scripts/argus_components/capture_system.py`
- Modify: `tests/test_capture_stream_post_process.py`
- Test: `tests/test_capture_async_job.py`

- [ ] **Step 1: Add a service-ordering test**

Add a focused test that constructs a fake `CaptureService`, starts one capture, and asserts:

```python
assert service.semantic_stencil_controller.apply_calls == 0
job = service.capture_once(cfg, capture_id="first", pose=pose)
assert isinstance(job, module.CaptureJob)
assert service.semantic_stencil_controller.apply_calls == 0
```

After two stable streaming ticks and the configured warm-up:

```python
assert service.semantic_stencil_controller.apply_calls == 1
assert capture_events == []
unreal_api.tick()
assert capture_events == ["capture"]
assert job.done is True
```

Reuse one fake stream and monkeypatch actor/component/asset lookups as in `test_capture_once_validates_session_and_rebinds_post_process_each_time`.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
python -m pytest tests/test_capture_async_job.py tests/test_capture_stream_post_process.py -q
```

Expected: FAIL because `CaptureService.capture_once()` still captures synchronously.

- [ ] **Step 3: Extract the final capture/export block**

Move the existing stream capture, file export, metadata construction, and compatibility fields into:

```python
def _capture_and_export(
    self,
    states,
    primary_stream,
    primary,
    intrinsics,
    play_session_plan,
    runtime_plan,
    semantic_stencil_stats,
    output_cfg,
    out_dir,
    capture_id,
    finalize,
):
    for state in states.values():
        self._capture_twice(state["component"])

    cid = capture_id or "{}_{}".format(
        output_cfg.get("file_prefix", "cap"),
        now_stamp(),
    )
    files = {}
    for name, state in states.items():
        stream = state["stream"]
        ext = self._choose_ext_by_rt(state["rt"])
        suffix = stream.file_suffix or name
        abs_path = os.path.join(out_dir, "{}_{}{}".format(cid, suffix, ext))
        self._export_rt(state["rt"], abs_path)
        if stream.force_png_opaque:
            self._make_png_opaque(abs_path)
        files[name] = abs_path

    primary_loc = primary["actor"].get_actor_location()
    primary_rot = primary["actor"].get_actor_rotation()
    intrinsics_meta = self.intrinsics_manager.intrinsics_to_metadata(
        primary["component"],
        intrinsics,
    )
    row = {
        "capture_id": cid,
        "x": primary_loc.x,
        "y": primary_loc.y,
        "z": primary_loc.z,
        "pitch": primary_rot.pitch,
        "yaw": primary_rot.yaw,
        "roll": primary_rot.roll,
        "files_json": json.dumps(files, ensure_ascii=False),
        "primary_stream": primary_stream.name,
        "runtime_play_session_plan_json": json.dumps(
            play_session_plan.to_metadata(),
            ensure_ascii=False,
        ),
        "runtime_plan_json": json.dumps(runtime_plan.to_metadata(), ensure_ascii=False),
        "semantic_stencil_json": json.dumps(semantic_stencil_stats, ensure_ascii=False),
        **intrinsics_meta,
    }
    if "rgb" in files:
        row["rgb_file"] = files["rgb"]
    if "mask" in files:
        row["mask_file"] = files["mask"]
    for name, path in files.items():
        row["{}_file".format(name)] = path
    return finalize(row) if finalize else row
```

- [ ] **Step 4: Return a started CaptureJob from capture_once**

Change the signature to:

```python
def capture_once(self, cfg, capture_id=None, pose=None, finalize=None):
```

After immediate component, pose, intrinsics, and runtime preparation, replace synchronous semantics/sleep/capture/export with:

```python
runtime_plan = self.runtime_controller.prepare_for_capture(
    cfg,
    pose=pose,
    capture_actor=primary["actor"],
)
cid = capture_id or "{}_{}".format(
    output_cfg.get("file_prefix", "cap"),
    now_stamp(),
)

def prepare_semantics():
    if runtime_plan.pause_after_warmup:
        self.runtime_controller.set_game_paused(True)
    return self.semantic_stencil_controller.apply(cfg, pose=pose)

def capture_ready(semantic_stats):
    return self._capture_and_export(
        states,
        primary_stream,
        primary,
        intrinsics,
        play_session_plan,
        runtime_plan,
        semantic_stats,
        output_cfg,
        out_dir,
        cid,
        finalize,
    )

return CaptureJob(
    capture_id=cid,
    runtime_plan=runtime_plan,
    is_streaming_completed=self.runtime_controller.is_streaming_completed,
    prepare_semantics=prepare_semantics,
    capture=capture_ready,
    cleanup=lambda: self.runtime_controller.finish_after_capture(runtime_plan),
).start()
```

Remove both synchronous `time.sleep()` calls from `capture_once()`; retain `import time` for `CaptureJob.time.monotonic`.

- [ ] **Step 5: Update the old post-process lifecycle test**

The existing test that deliberately raises during intrinsics resolution remains synchronous and should still pass unchanged. Add an assertion in a successful fake path that `capture_once()` returns a `CaptureJob` rather than a row.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest tests/test_capture_async_job.py tests/test_capture_stream_post_process.py tests/test_runtime_capture_controller.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add -- scripts/argus_components/capture_system.py tests/test_capture_async_job.py tests/test_capture_stream_post_process.py
git commit -m "Wait for streaming before semantic capture"
```

### Task 6: Convert single and batch entrypoints

**Files:**
- Modify: `scripts/capture_rgb_and_mask.py`
- Modify: `scripts/capture_pose_probe.py`
- Modify: `scripts/batch_capture.py`
- Create: `tests/test_async_batch_capture.py`
- Modify: `tests/test_capture_pose_probe.py`

- [ ] **Step 1: Write the serial batch-runner test**

Create `tests/test_async_batch_capture.py` with a fake service and jobs:

```python
def test_batch_runner_starts_next_capture_only_after_completion():
    service = FakeCaptureService()
    completed = []
    runner = BatchCaptureRunner(
        capture_service=service,
        cfg={},
        items=[
            {"capture_id": "a", "pose": {"x": 1}},
            {"capture_id": "b", "pose": {"x": 2}},
        ],
        finalize_row=lambda item, row: completed.append((item["capture_id"], row)) or row,
        continue_on_error=True,
    ).start()

    assert service.started_ids == ["a"]
    service.jobs[0].finish({"capture_id": "a"})
    assert service.started_ids == ["a", "b"]
    service.jobs[1].finish({"capture_id": "b"})

    assert runner.done is True
    assert runner.success_count == 2
    assert runner.failed_count == 0
    assert completed == [
        ("a", {"capture_id": "a"}),
        ("b", {"capture_id": "b"}),
    ]
```

The fake service must invoke `finalize` before completing each fake job so validation failures become job failures.

- [ ] **Step 2: Run the batch test and verify failure**

```powershell
python -m pytest tests/test_async_batch_capture.py -q
```

Expected: FAIL because `BatchCaptureRunner` does not exist.

- [ ] **Step 3: Add the minimal BatchCaptureRunner**

Add to `scripts/batch_capture.py` before `run_batch()`:

```python
class BatchCaptureRunner:
    def __init__(
        self,
        capture_service,
        cfg,
        items,
        finalize_row,
        continue_on_error,
        on_done=None,
    ):
        self.capture_service = capture_service
        self.cfg = cfg
        self.items = iter(items)
        self.finalize_row = finalize_row
        self.continue_on_error = bool(continue_on_error)
        self.on_done = on_done
        self.done = False
        self.error = None
        self.success_count = 0
        self.failed_count = 0

    def start(self):
        self._start_next()
        return self

    def _start_next(self):
        try:
            item = next(self.items)
        except StopIteration:
            self._finish()
            return

        try:
            job = self.capture_service.capture_once(
                self.cfg,
                capture_id=item["capture_id"],
                pose=item["pose"],
                finalize=lambda row: self.finalize_row(item, row),
            )
        except Exception as exc:
            self._fail(exc)
            return

        job.add_done_callback(self._job_done)

    def _job_done(self, job):
        if job.error is not None:
            self._fail(job.error)
            return
        self.success_count += 1
        self._start_next()

    def _fail(self, error):
        self.failed_count += 1
        if self.continue_on_error:
            self._start_next()
            return
        self.error = error
        self._finish()

    def _finish(self):
        self.done = True
        if self.on_done:
            self.on_done(self)
```

- [ ] **Step 4: Adapt run_batch to callbacks**

Keep all existing planning, skip, cleanup, and summary code. Build `capture_items` from rows whose action is neither `skip_duplicate_pose` nor `skip_existing`. Define `finalize_row(item, row)` to validate files, append metadata, update `completed_capture_ids`, and log success. Define `on_done(runner)` to print the existing final summary using runner counts. End `run_batch()` with:

```python
return BatchCaptureRunner(
    capture_service=capture_service,
    cfg=cfg,
    items=capture_items,
    finalize_row=finalize_row,
    continue_on_error=continue_on_error,
    on_done=on_done,
).start()
```

On failure, log the exception and formatted traceback inside `BatchCaptureRunner._fail()` before choosing continue or stop.

- [ ] **Step 5: Make the single entrypoint finalize asynchronously**

In `scripts/capture_rgb_and_mask.py`, define a local finalizer and pass it to the service:

```python
def finalize(row):
    file_map = validate_capture_outputs(row, expected_streams)
    metadata_csv = resolve_path(output_cfg["metadata_csv"])
    DataPipelineService().append_capture_metadata(metadata_csv, row)
    log("采集完成: {}".format(row["capture_id"]))
    for stream_name in expected_streams:
        log("{}: {}".format(stream_name.upper(), file_map.get(stream_name, "")))
    return row

return CaptureService().capture_once(
    cfg,
    capture_id=capture_id,
    pose=pose,
    finalize=finalize,
)
```

Remove the synchronous row handling after the old service call.

- [ ] **Step 6: Print pose-probe output from the completion callback**

Change `capture_pose_probe.main()` to:

```python
job = capture_once(capture_id=capture_id, pose=cfg["pose"])

def print_result(finished_job):
    if finished_job.error is not None:
        print("ARGUS_PROBE_ERROR={}".format(finished_job.error))
        return
    row = finished_job.result
    files = json.loads(row.get("files_json", "{}"))
    result = {"capture_id": row["capture_id"], "files": files}
    if "rgb" in files:
        result["rgb_file"] = files["rgb"]
    if "mask" in files:
        result["mask_file"] = files["mask"]
    print("ARGUS_PROBE_RESULT={}".format(json.dumps(result, ensure_ascii=False)))

job.add_done_callback(print_result)
return job
```

- [ ] **Step 7: Run entrypoint tests**

```powershell
python -m pytest tests/test_async_batch_capture.py tests/test_capture_pose_probe.py tests/test_entrypoint_bootstrap.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```powershell
git add -- scripts/batch_capture.py scripts/capture_rgb_and_mask.py scripts/capture_pose_probe.py tests/test_async_batch_capture.py tests/test_capture_pose_probe.py
git commit -m "Run Argus capture entrypoints asynchronously"
```

### Task 7: Full verification and CitySample acceptance

**Files:**
- Modify only if verification exposes a defect in the files above.

- [ ] **Step 1: Run all automated checks**

```powershell
python -m pytest -q
python -m compileall -q argus_core argus_backends scripts tests
git diff --check
```

Expected: all tests pass, compileall is silent, and diff check reports no errors.

- [ ] **Step 2: Start a fresh PIE session**

Use the UE editor PIE control with `FastGeo.EnableTransformer 0` already applied at the play-session boundary. Do not call `finish_loading_before_screenshot()`.

- [ ] **Step 3: Capture the harbor pose once**

Run `scripts/capture_pose_probe.py` against the harbor probe config. Wait for `ARGUS_PROBE_RESULT`; do not issue a second capture to warm the scene.

Expected:

- The job logs at least one streaming wait Tick.
- Runtime semantic scan is close to the previously warmed count (~29k components), not the failed first count (~20k).
- Mask taxonomy validity is 100%.
- Unknown is no longer a large unloaded-world region; target is below 1% for the verified harbor pose.

- [ ] **Step 4: Recheck the three existing good scenes**

Capture dense core, freeway, and street oblique once each.

Expected: taxonomy validity remains 100% and class diversity does not regress from the previous 12/14/13-class results.

- [ ] **Step 5: Verify a two-frame batch**

Run two known poses through `batch_capture.py`.

Expected: the second Job starts only after the first finishes; both output sets exist and both metadata rows are appended exactly once.

- [ ] **Step 6: Stop PIE and restore the editor state**

Stop PIE and confirm `FastGeo.EnableTransformer 1` is restored.

- [ ] **Step 7: Review repository state**

```powershell
git status --short --branch
git log -8 --oneline --decorate
```

Expected: only `AGENTS.md` remains untracked unless an acceptance defect required an additional committed fix.
