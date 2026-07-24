# Player-Operated Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Editor/PIE-only `F9` capture action to CitySample Photo Mode that captures Argus RGB, semantic mask, and metadata from the exact visible player camera without moving the player.

**Architecture:** CitySample keeps ownership of Photo Mode movement and Enhanced Input. A tiny editor-only C++ binding invokes a persistent Argus Python module; that module reads `PlayerCameraManager`, applies per-call runtime overrides, and delegates to the existing asynchronous `CaptureJob`. No new Pawn, HUD, scheduler, queue, or plugin is introduced.

**Tech Stack:** Python 3, pytest, Unreal Engine 5.8 Python API, CitySample C++, Enhanced Input, PythonScriptPlugin, direct Unreal MCP/Slate automation.

---

## Scope and constraints

- Argus workspace: `D:\Study\Code\Python\UE\cv\Argus`
- CitySample project: `E:\UnrealProject\CitySample`
- UE 5.8 engine: `E:\UE_5.8`
- CitySample is not a Git repository. Back up every external source/uasset before editing it.
- Keep `AGENTS.md` untracked.
- The current branch is already an isolated feature branch/worktree: `argus/async-world-partition-capture`.
- `PythonScriptPlugin` is `UncookedOnly`; the integration is Editor/PIE-only and the module dependency must be added only for editor builds.
- Use `F9` only. Do not add a controller binding, UI panel, queue, cancel flow, or packaged-runtime support.

## File map

- Modify `scripts/capture_rgb_and_mask.py`: expose the existing finalize path for an already loaded config.
- Create `scripts/capture_player_view.py`: own player-camera pose extraction, per-call config overrides, active-job retention, and status messages.
- Modify `tests/test_async_capture_entrypoint.py`: drive the shared-config entrypoint test-first.
- Create `tests/test_capture_player_view.py`: cover player pose, reentry, completion, and failure behavior.
- Create `tests/test_citysample_photo_mode_capture_integration.py`: local source-contract check for the external CitySample glue.
- Modify `docs/workflow.md`: document the interactive capture workflow.
- Modify external `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.h`.
- Modify external `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.cpp`.
- Modify external `E:\UnrealProject\CitySample\Source\CitySample\CitySample.Build.cs`.
- Create external `/Game/Input/PhotoMode/IA_PM_ArgusCapture`.
- Modify external `/Game/Input/PhotoMode/IM_PM_Simple_MappingContext`.
- Modify external `/Game/Gameplay/Framework/BP_PhotoModeComponent`.

### Task 1: Reuse the existing single-capture finalize path

**Files:**
- Modify: `tests/test_async_capture_entrypoint.py`
- Modify: `scripts/capture_rgb_and_mask.py:167-202`

- [ ] **Step 1: Write the failing shared-config entrypoint test**

Append this test to `tests/test_async_capture_entrypoint.py`:

```python
def test_capture_with_config_returns_job_and_finalizes_metadata(monkeypatch, tmp_path):
    FakePipeline.appended = []
    rgb_path = tmp_path / "rgb.png"
    mask_path = tmp_path / "mask.png"
    rgb_path.write_bytes(b"rgb")
    mask_path.write_bytes(b"mask")
    metadata_path = str(tmp_path / "metadata.csv")
    cfg = {
        "capture": {},
        "output": {
            "capture_dir": str(tmp_path),
            "metadata_csv": metadata_path,
        },
    }
    module = import_entrypoint(monkeypatch, cfg)

    job = module.capture_with_config(cfg, capture_id="configured")

    assert job is FakeCaptureService.instance.job
    row = {
        "capture_id": "configured",
        "files_json": json.dumps(
            {"rgb": str(rgb_path), "mask": str(mask_path)},
            ensure_ascii=False,
        ),
    }
    assert FakeCaptureService.instance.finalize(row) is row
    assert FakePipeline.appended == [(metadata_path, row)]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_async_capture_entrypoint.py::test_capture_with_config_returns_job_and_finalizes_metadata -q
```

Expected: FAIL with `AttributeError: module 'scripts.capture_rgb_and_mask' has no attribute 'capture_with_config'`.

- [ ] **Step 3: Extract the minimal shared-config function**

Replace the current `capture_once()` body in `scripts/capture_rgb_and_mask.py` with these two functions; retain the existing module-level `if __name__ == "__main__": capture_once()` block:

```python
def capture_with_config(cfg, capture_id=None, pose=None):
    """Start one capture from an already loaded pipeline config."""
    output_cfg = cfg["output"]
    expected_streams = expected_stream_names(cfg)

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


def capture_once(config_path=None, capture_id=None, pose=None):
    """Load the pipeline config and start one asynchronous capture."""
    cfg, _ = load_json_config(config_path)
    return capture_with_config(cfg, capture_id=capture_id, pose=pose)
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_async_capture_entrypoint.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit the refactor**

```powershell
git add -- scripts/capture_rgb_and_mask.py tests/test_async_capture_entrypoint.py
git commit -m "Reuse single capture finalization"
```

### Task 2: Capture the exact player camera and retain one active Job

**Files:**
- Create: `tests/test_capture_player_view.py`
- Create: `scripts/capture_player_view.py`

- [ ] **Step 1: Write the failing player-view tests**

Create `tests/test_capture_player_view.py` with:

```python
import importlib
import sys
import types

import pytest


class FakeVector:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class FakeRotator:
    def __init__(self, pitch, yaw, roll):
        self.pitch = pitch
        self.yaw = yaw
        self.roll = roll


class FakeCameraManager:
    def get_camera_location(self):
        return FakeVector(10.0, 20.0, 30.0)

    def get_camera_rotation(self):
        return FakeRotator(-12.0, 45.0, 1.5)

    def get_fov_angle(self):
        return 73.0


class FakeController:
    def __init__(self):
        self.camera_manager = FakeCameraManager()

    def get_editor_property(self, name):
        assert name == "player_camera_manager"
        return self.camera_manager


class FakeJob:
    def __init__(self):
        self.done = False
        self.result = None
        self.error = None
        self.capture_id = "player_job"
        self.callback = None

    def add_done_callback(self, callback):
        self.callback = callback

    def finish(self, result=None, error=None):
        self.done = True
        self.result = result
        self.error = error
        self.callback(self)


class FakeCaptureEntrypoint:
    def __init__(self):
        self.calls = []
        self.error = None
        self.job = FakeJob()

    def capture_with_config(self, cfg, capture_id=None, pose=None):
        if self.error:
            raise self.error
        self.calls.append((cfg, capture_id, pose))
        return self.job


def import_player_capture(monkeypatch, world=object()):
    messages = []
    controller = FakeController()
    capture_entrypoint = FakeCaptureEntrypoint()
    cfg = {
        "runtime": {
            "move_player_to_capture": True,
            "restore_player_after_capture": True,
        }
    }
    subsystem = types.SimpleNamespace(get_game_world=lambda: world)
    fake_unreal = types.SimpleNamespace(
        UnrealEditorSubsystem=object(),
        get_editor_subsystem=lambda cls: subsystem,
        GameplayStatics=types.SimpleNamespace(
            get_player_controller=lambda current_world, index: (
                controller if current_world is not None and index == 0 else None
            )
        ),
        SystemLibrary=types.SimpleNamespace(
            print_string=lambda context, message, *args: messages.append(message)
        ),
    )
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    monkeypatch.setitem(
        sys.modules,
        "common",
        types.SimpleNamespace(load_json_config=lambda path=None: (cfg, "config.json")),
    )
    monkeypatch.setitem(
        sys.modules,
        "capture_rgb_and_mask",
        types.SimpleNamespace(capture_with_config=capture_entrypoint.capture_with_config),
    )
    sys.modules.pop("scripts.capture_player_view", None)
    module = importlib.import_module("scripts.capture_player_view")
    return module, capture_entrypoint, messages


def test_capture_uses_player_camera_pose_and_disables_player_move(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)

    job = module.capture_player_view()

    assert job is entrypoint.job
    cfg, capture_id, pose = entrypoint.calls[0]
    assert capture_id is None
    assert cfg["runtime"]["move_player_to_capture"] is False
    assert cfg["runtime"]["restore_player_after_capture"] is False
    assert pose == {
        "x": 10.0,
        "y": 20.0,
        "z": 30.0,
        "pitch": -12.0,
        "yaw": 45.0,
        "roll": 1.5,
        "fov_deg": 73.0,
    }
    assert messages == ["Argus capture started"]


def test_capture_reuses_the_active_job(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)

    first = module.capture_player_view()
    second = module.capture_player_view()

    assert second is first
    assert len(entrypoint.calls) == 1
    assert messages[-1] == "Argus capture already in progress"


def test_success_reports_capture_id_and_clears_active_job(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)
    module.capture_player_view()

    entrypoint.job.finish(result={"capture_id": "player_001"})

    assert module._active_job is None
    assert messages[-1] == "Argus captured: player_001"


def test_async_failure_reports_error_and_clears_active_job(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)
    module.capture_player_view()

    entrypoint.job.finish(error=RuntimeError("capture failed"))

    assert module._active_job is None
    assert messages[-1] == "Argus capture failed: capture failed"


def test_sync_failure_reports_error_and_leaves_no_active_job(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)
    entrypoint.error = RuntimeError("startup failed")

    with pytest.raises(RuntimeError, match="startup failed"):
        module.capture_player_view()

    assert module._active_job is None
    assert messages[-1] == "Argus capture failed: startup failed"


def test_capture_requires_a_pie_world(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch, world=None)

    with pytest.raises(RuntimeError, match="PIE world"):
        module.capture_player_view()

    assert entrypoint.calls == []
    assert messages[-1] == "Argus capture failed: No PIE world is running"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest tests/test_capture_player_view.py -q
```

Expected: collection/import FAIL because `scripts.capture_player_view` does not exist.

- [ ] **Step 3: Implement the minimal persistent entrypoint**

Create `scripts/capture_player_view.py` with:

```python
"""Capture Argus streams from the current PIE player camera."""

import unreal

from capture_rgb_and_mask import capture_with_config
from common import load_json_config


_active_job = None


def _notify(context, message):
    unreal.SystemLibrary.print_string(context, message, True, True)


def _get_player_controller():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world() if subsystem else None

    if not world:
        raise RuntimeError("No PIE world is running")

    controller = unreal.GameplayStatics.get_player_controller(world, 0)

    if not controller:
        raise RuntimeError("No local player controller is available")

    return controller


def _camera_pose(controller):
    camera = controller.get_editor_property("player_camera_manager")

    if not camera:
        raise RuntimeError("No PlayerCameraManager is available")

    location = camera.get_camera_location()
    rotation = camera.get_camera_rotation()
    return {
        "x": float(location.x),
        "y": float(location.y),
        "z": float(location.z),
        "pitch": float(rotation.pitch),
        "yaw": float(rotation.yaw),
        "roll": float(rotation.roll),
        "fov_deg": float(camera.get_fov_angle()),
    }


def _finish_capture(job, context):
    global _active_job

    if _active_job is job:
        _active_job = None

    if job.error:
        _notify(context, "Argus capture failed: {}".format(job.error))
        return

    capture_id = (job.result or {}).get("capture_id", job.capture_id)
    _notify(context, "Argus captured: {}".format(capture_id))


def capture_player_view():
    global _active_job

    if _active_job is not None and not _active_job.done:
        _notify(None, "Argus capture already in progress")
        return _active_job

    context = None

    try:
        context = _get_player_controller()
        cfg, _ = load_json_config()
        runtime_cfg = cfg.setdefault("runtime", {})
        runtime_cfg["move_player_to_capture"] = False
        runtime_cfg["restore_player_after_capture"] = False
        pose = _camera_pose(context)
        _notify(context, "Argus capture started")
        job = capture_with_config(cfg, pose=pose)
        _active_job = job
        job.add_done_callback(lambda completed: _finish_capture(completed, context))
        return job
    except Exception as exc:
        _active_job = None
        _notify(context, "Argus capture failed: {}".format(exc))
        raise
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_capture_player_view.py tests/test_async_capture_entrypoint.py -q
```

Expected: `8 passed`.

- [ ] **Step 5: Commit the player-view entrypoint**

```powershell
git add -- scripts/capture_player_view.py tests/test_capture_player_view.py
git commit -m "Capture from the player camera"
```

### Task 3: Add the minimal CitySample Photo Mode glue

**Files:**
- Create: `tests/test_citysample_photo_mode_capture_integration.py`
- Modify external: `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.h`
- Modify external: `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.cpp`
- Modify external: `E:\UnrealProject\CitySample\Source\CitySample\CitySample.Build.cs`

- [ ] **Step 1: Save the current editor state before backup**

With PIE stopped, execute this in the UE Python console through the direct MCP Slate console:

```python
import unreal
assert unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
```

Expected: the command succeeds and no dirty package save error appears in the Output Log.

- [ ] **Step 2: Back up every external file that will be edited**

Run in PowerShell:

```powershell
$backupRoot = 'E:\UnrealProject\CitySample\ArgusBackups\20260724_player_capture'
if (Test-Path -LiteralPath $backupRoot) { throw "Backup already exists: $backupRoot" }
New-Item -ItemType Directory -Path "$backupRoot\Source" -Force | Out-Null
New-Item -ItemType Directory -Path "$backupRoot\Content" -Force | Out-Null
Copy-Item -LiteralPath 'E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.h' -Destination "$backupRoot\Source\PhotoModeComponent.h"
Copy-Item -LiteralPath 'E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.cpp' -Destination "$backupRoot\Source\PhotoModeComponent.cpp"
Copy-Item -LiteralPath 'E:\UnrealProject\CitySample\Source\CitySample\CitySample.Build.cs' -Destination "$backupRoot\Source\CitySample.Build.cs"
Copy-Item -LiteralPath 'E:\UnrealProject\CitySample\Content\Input\PhotoMode\IM_PM_Simple_MappingContext.uasset' -Destination "$backupRoot\Content\IM_PM_Simple_MappingContext.uasset"
Copy-Item -LiteralPath 'E:\UnrealProject\CitySample\Content\Gameplay\Framework\BP_PhotoModeComponent.uasset' -Destination "$backupRoot\Content\BP_PhotoModeComponent.uasset"
Get-ChildItem -LiteralPath $backupRoot -Recurse | Select-Object FullName,Length
```

Expected: five non-empty backup files under the exact backup root.

- [ ] **Step 3: Write the failing source-contract test**

Create `tests/test_citysample_photo_mode_capture_integration.py` with:

```python
from pathlib import Path

import pytest


CITYSAMPLE_ROOT = Path(r"E:\UnrealProject\CitySample")
pytestmark = pytest.mark.skipif(
    not CITYSAMPLE_ROOT.exists(),
    reason="CitySample local integration is not installed",
)


def test_photo_mode_source_contains_argus_capture_binding():
    header = (
        CITYSAMPLE_ROOT / "Source/CitySample/Camera/PhotoModeComponent.h"
    ).read_text(encoding="utf-8-sig")
    source = (
        CITYSAMPLE_ROOT / "Source/CitySample/Camera/PhotoModeComponent.cpp"
    ).read_text(encoding="utf-8-sig")
    build = (
        CITYSAMPLE_ROOT / "Source/CitySample/CitySample.Build.cs"
    ).read_text(encoding="utf-8-sig")

    assert "UInputAction* CaptureAction" in header
    assert "void CaptureActionBinding();" in header
    assert "BindAction(CaptureAction, ETriggerEvent::Started" in source
    assert "IPythonScriptPlugin" in source
    assert "capture_player_view.capture_player_view()" in source
    editor_block = build[build.index("if (Target.bBuildEditor == true)") :]
    assert 'PrivateDependencyModuleNames.Add("PythonScriptPlugin")' in editor_block
```

- [ ] **Step 4: Run the source-contract test and verify RED**

Run:

```powershell
python -m pytest tests/test_citysample_photo_mode_capture_integration.py -q
```

Expected: FAIL on the first missing `CaptureAction` assertion.

- [ ] **Step 5: Patch `PhotoModeComponent.h`**

Add the new property immediately after `UseAutoFocusAction`:

```cpp
	UPROPERTY(EditDefaultsOnly, Category = "Input")
	class UInputAction* CaptureAction;
```

Add the binding declaration immediately after `DisableAutoFocusActionBinding()`:

```cpp
	void CaptureActionBinding();
```

- [ ] **Step 6: Patch `PhotoModeComponent.cpp`**

Add the editor-only include after the Enhanced Input includes:

```cpp
#if WITH_EDITOR
#include "IPythonScriptPlugin.h"
#endif
```

Add this binding in `SetUpInputs()` after the autofocus bindings:

```cpp
#if WITH_EDITOR
		if (CaptureAction)
		{
			EnhancedInputComponent->BindAction(CaptureAction, ETriggerEvent::Started, this, &ThisClass::CaptureActionBinding);
		}
#endif
```

Append this function after `DisableAutoFocusActionBinding()`:

```cpp
void UPhotoModeComponent::CaptureActionBinding()
{
#if WITH_EDITOR
	if (State != EPhotoModeState::Active)
	{
		return;
	}

	IPythonScriptPlugin* const PythonPlugin = IPythonScriptPlugin::Get();
	if (!PythonPlugin)
	{
		UE_LOG(LogCitySamplePhotoMode, Error, TEXT("Argus capture failed: PythonScriptPlugin is unavailable."));
		return;
	}

	if (!PythonPlugin->IsPythonInitialized())
	{
		PythonPlugin->ForceEnablePythonAtRuntime();
	}

	if (!PythonPlugin->IsPythonInitialized())
	{
		UE_LOG(LogCitySamplePhotoMode, Error, TEXT("Argus capture failed: Python is not initialized."));
		return;
	}

	static const TCHAR* const Command = TEXT(
		"import sys; "
		"p=r'D:/Study/Code/Python/UE/cv/Argus/scripts'; "
		"sys.path.insert(0, p) if p not in sys.path else None; "
		"import capture_player_view; "
		"capture_player_view.capture_player_view()"
	);

	if (!PythonPlugin->ExecPythonCommand(Command))
	{
		UE_LOG(LogCitySamplePhotoMode, Error, TEXT("Argus capture Python command failed."));
	}
#endif
}
```

The fixed local path is intentional for this workstation. Do not add a settings object until the integration needs to move between machines.

- [ ] **Step 7: Patch `CitySample.Build.cs`**

Inside the existing `if (Target.bBuildEditor == true)` block, after the editor public dependencies, add:

```csharp
			PrivateDependencyModuleNames.Add("PythonScriptPlugin");
```

- [ ] **Step 8: Run the source-contract test and verify GREEN**

Run:

```powershell
python -m pytest tests/test_citysample_photo_mode_capture_integration.py -q
```

Expected: `1 passed`.

- [ ] **Step 9: Gracefully stop the editor before the UHT/full build**

Execute in the UE Python console:

```python
import unreal
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
unreal.SystemLibrary.quit_editor()
```

Then confirm no editor process remains:

```powershell
Get-Process UnrealEditor -ErrorAction SilentlyContinue
```

Expected: no process output.

- [ ] **Step 10: Build the CitySample Editor target**

Run:

```powershell
& 'E:\UE_5.8\Engine\Build\BatchFiles\Build.bat' CitySampleEditor Win64 Development '-Project=E:\UnrealProject\CitySample\CitySample.uproject' -WaitMutex -FromMsBuild
```

Expected: `BUILD SUCCESSFUL` and exit code `0`.

- [ ] **Step 11: Commit the local integration contract**

```powershell
git add -- tests/test_citysample_photo_mode_capture_integration.py
git commit -m "Check CitySample capture integration"
```

### Task 4: Create and connect the Enhanced Input assets

**Files:**
- Create external: `E:\UnrealProject\CitySample\Content\Input\PhotoMode\IA_PM_ArgusCapture.uasset`
- Modify external: `E:\UnrealProject\CitySample\Content\Input\PhotoMode\IM_PM_Simple_MappingContext.uasset`
- Modify external: `E:\UnrealProject\CitySample\Content\Gameplay\Framework\BP_PhotoModeComponent.uasset`

- [ ] **Step 1: Restart CitySample Editor visibly and wait for MCP**

Run:

```powershell
Start-Process -FilePath 'E:\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe' -ArgumentList '"E:\UnrealProject\CitySample\CitySample.uproject"'
```

Wait until `http://127.0.0.1:13001/mcp` responds to `initialize` and the editor Output Log has no module-load error.

- [ ] **Step 2: Create the action, map `F9`, and set the Blueprint CDO**

Use SlateInspector `Snapshot` to rediscover the UE Python console text box, then submit this command with `Type`:

```python
import unreal

action_path = "/Game/Input/PhotoMode/IA_PM_ArgusCapture"
source_action_path = "/Game/Input/PhotoMode/IA_PM_AutoFocus"
context_path = "/Game/Input/PhotoMode/IM_PM_Simple_MappingContext"
blueprint_path = "/Game/Gameplay/Framework/BP_PhotoModeComponent"

if not unreal.EditorAssetLibrary.does_asset_exist(action_path):
    assert unreal.EditorAssetLibrary.duplicate_asset(source_action_path, action_path)

action = unreal.EditorAssetLibrary.load_asset(action_path)
context = unreal.EditorAssetLibrary.load_asset(context_path)
assert action and context

action.set_editor_property("value_type", unreal.InputActionValueType.BOOLEAN)
key = unreal.Key("F9")
context.unmap_key(action, key)
context.map_key(action, key)

blueprint_class = unreal.EditorAssetLibrary.load_blueprint_class(blueprint_path)
assert blueprint_class
default_object = unreal.get_default_object(blueprint_class)
default_object.set_editor_property("capture_action", action)

assert unreal.EditorAssetLibrary.save_asset(action_path)
assert unreal.EditorAssetLibrary.save_asset(context_path)
assert unreal.EditorAssetLibrary.save_asset(blueprint_path)
print("ARGUS_PLAYER_CAPTURE_ASSETS_READY")
```

Expected: Python console prints `ARGUS_PLAYER_CAPTURE_ASSETS_READY`.

- [ ] **Step 3: Verify the saved action, mapping, and CDO reference**

Submit this second UE Python command:

```python
import unreal

action_path = "/Game/Input/PhotoMode/IA_PM_ArgusCapture"
context_path = "/Game/Input/PhotoMode/IM_PM_Simple_MappingContext"
blueprint_path = "/Game/Gameplay/Framework/BP_PhotoModeComponent"

action = unreal.EditorAssetLibrary.load_asset(action_path)
context = unreal.EditorAssetLibrary.load_asset(context_path)
default_object = unreal.get_default_object(
    unreal.EditorAssetLibrary.load_blueprint_class(blueprint_path)
)
mappings = context.get_editor_property("default_key_mappings").get_editor_property("mappings")

assert action.get_editor_property("value_type") == unreal.InputActionValueType.BOOLEAN
assert default_object.get_editor_property("capture_action") == action
assert any(
    mapping.get_editor_property("action") == action
    and str(mapping.get_editor_property("key")) == "F9"
    for mapping in mappings
)
print("ARGUS_PLAYER_CAPTURE_ASSETS_VERIFIED")
```

Expected: Python console prints `ARGUS_PLAYER_CAPTURE_ASSETS_VERIFIED`.

### Task 5: Document usage and run repository verification

**Files:**
- Modify: `docs/workflow.md`

- [ ] **Step 1: Add the interactive workflow**

Append this section to `docs/workflow.md`:

```markdown
## 22. Photo Mode 交互捕捉

交互捕捉只支持 Unreal Editor 的 PIE：

1. 在 PIE 之外运行 `scripts/prepare_runtime_play_session.py`。
2. 启动 PIE 并进入 CitySample Photo Mode。
3. 使用原有 `W/A/S/D`、鼠标、`Q/E` 自由探索。
4. 按 `F9` 捕捉当前最终玩家相机视角。
5. 等待屏幕显示 `Argus captured: <capture_id>` 后再拍下一张。
6. 停止 PIE 后运行 `scripts/restore_runtime_play_session.py`。

交互入口从 `PlayerCameraManager` 读取位置、旋转和 FOV，并只对本次捕捉关闭 `move_player_to_capture`，不会修改磁盘配置或批量捕捉行为。重复按键不会排队，只保留当前活动 Job。输出仍写入 `output/captures/` 和 `output/capture_metadata.csv`。
```

Also add this row to the entrypoint script table in section 4:

```markdown
| `capture_player_view.py` | 从 CitySample Photo Mode 当前玩家相机触发交互单帧采集 |
```

- [ ] **Step 2: Run the full Python test suite**

Run:

```powershell
python -m pytest -q
```

Expected baseline after this plan: `111 passed`.

- [ ] **Step 3: Compile every Python module**

Run:

```powershell
python -m compileall -q argus_core argus_backends scripts tests
```

Expected: exit code `0` with no output.

- [ ] **Step 4: Check whitespace and worktree scope**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intended tracked changes plus untracked `AGENTS.md`.

- [ ] **Step 5: Commit the workflow documentation**

```powershell
git add -- docs/workflow.md
git commit -m "Document Photo Mode capture"
```

### Task 6: Run live UE 5.8 acceptance

**Files:**
- Verify: `output/capture_metadata.csv`
- Verify: `output/captures/*_rgb.png`
- Verify: `output/captures/*_mask.png`

- [ ] **Step 1: Prepare CitySample before PIE**

With PIE stopped, submit in the UE Python console:

```python
import runpy
runpy.run_path(
    r"D:\Study\Code\Python\UE\cv\Argus\scripts\prepare_runtime_play_session.py",
    run_name="__main__",
)
```

Expected: `FastGeo.EnableTransformer` is verified as `0` and the Argus runtime state file is written.

- [ ] **Step 2: Start in-process PIE**

Call direct MCP tool `EditorToolset.EditorAppToolset.StartPIE` with:

```json
{
  "options": {
    "bSimulate": false,
    "playMode": "PlayMode_InViewPort",
    "warmupSeconds": 5.0
  }
}
```

Expected: `EditorToolset.EditorAppToolset.IsPIERunning` returns `true`.

- [ ] **Step 3: Enter Photo Mode and focus the PIE viewport**

Use the UE Python console to activate the existing component:

```python
import unreal
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
controller = unreal.GameplayStatics.get_player_controller(world, 0)
component = controller.get_component_by_class(unreal.PhotoModeComponent)
assert component and component.activate_photo_mode()
print("ARGUS_PHOTO_MODE_ACTIVE")
```

Use SlateInspector `Windows(action="list")`, select the PIE/editor window, click the PIE viewport, and confirm movement input is accepted.

- [ ] **Step 4: Trigger the first capture through the real `F9` binding**

Record the current camera pose in a temporary acceptance file from the UE Python console:

```python
import json
from pathlib import Path

camera = controller.get_editor_property("player_camera_manager")
location = camera.get_camera_location()
rotation = camera.get_camera_rotation()
expected_path = Path(
    r"D:\Study\Code\Python\UE\cv\Argus\output\player_capture_acceptance_expected.json"
)
expected = [{
    "x": location.x,
    "y": location.y,
    "z": location.z,
    "pitch": rotation.pitch,
    "yaw": rotation.yaw,
    "roll": rotation.roll,
    "fov": camera.get_fov_angle(),
}]
expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8")
print("ARGUS_EXPECTED_CAMERA", expected[0])
```

With the PIE viewport focused, call SlateInspector `PressKey` with:

```json
{"key": "F9"}
```

Immediately call it a second time. Expected: one `Argus capture started`, one `Argus capture already in progress`, and only one completed capture/metadata row.

- [ ] **Step 5: Move to a deterministic second view and capture again**

After the first Job finishes, submit this command in the UE Python console:

```python
pawn = controller.get_controlled_pawn()
assert pawn
current_location = pawn.get_actor_location()
current_rotation = pawn.get_actor_rotation()
pawn.set_actor_location_and_rotation(
    unreal.Vector(current_location.x + 2000.0, current_location.y, current_location.z + 300.0),
    unreal.Rotator(
        pitch=current_rotation.pitch - 5.0,
        yaw=current_rotation.yaw + 30.0,
        roll=current_rotation.roll,
    ),
    False,
    True,
)
print("ARGUS_SECOND_VIEW_MOVED")
```

Wait at least two Slate ticks, then submit this second command to record the updated final camera:

```python
import json
from pathlib import Path

camera = controller.get_editor_property("player_camera_manager")
location = camera.get_camera_location()
rotation = camera.get_camera_rotation()
expected_path = Path(
    r"D:\Study\Code\Python\UE\cv\Argus\output\player_capture_acceptance_expected.json"
)
expected = json.loads(expected_path.read_text(encoding="utf-8"))
expected.append({
    "x": location.x,
    "y": location.y,
    "z": location.z,
    "pitch": rotation.pitch,
    "yaw": rotation.yaw,
    "roll": rotation.roll,
    "fov": camera.get_fov_angle(),
})
expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8")
print("ARGUS_EXPECTED_CAMERA", expected[-1])
```

Focus the PIE viewport and call SlateInspector `PressKey` with `{"key":"F9"}` once.

Expected: a second distinct capture ID is produced and Photo Mode movement/look/altitude/autofocus still work.

- [ ] **Step 6: Validate metadata pose, files, and taxonomy colors**

Run this PowerShell-hosted Python check after both jobs complete:

```powershell
@'
import csv
import json
from pathlib import Path

from PIL import Image

root = Path(r"D:\Study\Code\Python\UE\cv\Argus")
with (root / "output/capture_metadata.csv").open(encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))[-2:]
expected = json.loads(
    (root / "output/player_capture_acceptance_expected.json").read_text(encoding="utf-8")
)

assert len(rows) == 2
assert len(expected) == 2
assert rows[0]["capture_id"] != rows[1]["capture_id"]

taxonomy = set()
with (root / "config/semantic_classes.csv").open(encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle):
        taxonomy.add((int(row["r"]), int(row["g"]), int(row["b"])))

for row, camera in zip(rows, expected):
    files = json.loads(row["files_json"])
    assert Path(files["rgb"]).is_file()
    assert Path(files["mask"]).is_file()
    for field in ("x", "y", "z", "pitch", "yaw", "roll"):
        assert abs(float(row[field]) - float(camera[field])) < 0.05, (field, row[field], camera[field])
    assert abs(float(row["fov"]) - float(camera["fov"])) < 0.01
    colors = set(Image.open(files["mask"]).convert("RGB").getdata())
    invalid = colors - taxonomy
    assert not invalid, (row["capture_id"], sorted(invalid)[:20])
    print(row["capture_id"], "colors=", len(colors), "invalid=0", "fov=", row["fov"])
'@ | python -
```

Expected: two capture IDs, exact pose/FOV checks pass within tolerance, both RGB/mask paths exist, and both masks report `invalid=0`.

- [ ] **Step 7: Stop PIE and restore the pre-PIE state**

Call direct MCP tool `EditorToolset.EditorAppToolset.StopPIE`, then submit:

```python
import runpy
runpy.run_path(
    r"D:\Study\Code\Python\UE\cv\Argus\scripts\restore_runtime_play_session.py",
    run_name="__main__",
)
```

Expected: PIE is stopped, `FastGeo.EnableTransformer` returns to its recorded original value, and the Argus runtime state file is removed.

- [ ] **Step 8: Run final fresh verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q argus_core argus_backends scripts tests
git diff --check
git status --short --branch
```

Expected: all tests pass, compileall exits `0`, no whitespace errors, and only `AGENTS.md` remains untracked.

## Local UE 5.8 references used by this plan

- `D:\UE58Knowledge\web\markdown\city-sample-project-unreal-engine-demonstration.md`
- `D:\UE58Knowledge\web\markdown\cameras-in-unreal-engine.md`
- `D:\UE58Knowledge\web\markdown\scripting-the-unreal-editor-using-python.md`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.h`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.cpp`
- `E:\UnrealProject\CitySample\Source\CitySample\CitySample.Build.cs`
- `E:\UE_5.8\Engine\Source\Runtime\Engine\Classes\Camera\PlayerCameraManager.h`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\PythonScriptPlugin.uplugin`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Public\IPythonScriptPlugin.h`
- `E:\UE_5.8\Engine\Plugins\EnhancedInput\Source\EnhancedInput\Public\InputMappingContext.h`
- `E:\UE_5.8\Engine\Plugins\EnhancedInput\Source\EnhancedInput\Public\EnhancedInputSubsystemInterface.h`
