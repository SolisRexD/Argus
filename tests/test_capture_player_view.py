import importlib
import sys
import types

import pytest


class FakeVector:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z


class FakeRotator:
    def __init__(self, pitch, yaw, roll): self.pitch, self.yaw, self.roll = pitch, yaw, roll


class FakeCameraManager:
    def get_camera_location(self): return FakeVector(10.0, 20.0, 30.0)
    def get_camera_rotation(self): return FakeRotator(-12.0, 45.0, 1.5)
    def get_fov_angle(self): return 73.0


class FakeController:
    def __init__(self): self.camera_manager = FakeCameraManager()
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
        self.callback_error = None

    def add_done_callback(self, callback):
        if self.callback_error:
            raise self.callback_error
        self.callback = callback
        if self.done:
            callback(self)

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
    cfg = {"runtime": {"move_player_to_capture": True, "restore_player_after_capture": True}}
    subsystem = types.SimpleNamespace(get_game_world=lambda: world)
    fake_unreal = types.SimpleNamespace(
        UnrealEditorSubsystem=object(),
        get_editor_subsystem=lambda cls: subsystem,
        GameplayStatics=types.SimpleNamespace(
            get_player_controller=lambda current_world, index: controller if current_world is not None and index == 0 else None
        ),
        SystemLibrary=types.SimpleNamespace(
            print_string=lambda context, message, *args: messages.append(message)
        ),
    )
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    monkeypatch.setitem(sys.modules, "common", types.SimpleNamespace(load_json_config=lambda path=None: (cfg, "config.json")))
    monkeypatch.setitem(sys.modules, "capture_rgb_and_mask", types.SimpleNamespace(capture_with_config=capture_entrypoint.capture_with_config))
    sys.modules.pop("scripts.capture_player_view", None)
    module = importlib.import_module("scripts.capture_player_view")
    return module, capture_entrypoint, messages


def test_capture_uses_player_camera_pose_and_disables_player_move(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)

    job = module.capture_player_view()

    assert job is entrypoint.job
    cfg, capture_id, pose = entrypoint.calls[0]
    assert capture_id is None
    assert cfg["runtime"] == {
        "move_player_to_capture": False,
        "restore_player_after_capture": False,
    }
    assert pose == {
        "x": 10.0, "y": 20.0, "z": 30.0,
        "pitch": -12.0, "yaw": 45.0, "roll": 1.5,
        "fov_deg": 73.0,
    }
    assert messages[0] == "Argus capture started"


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


def test_completed_job_reports_success_and_clears_active_job_immediately(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)
    entrypoint.job.done = True
    entrypoint.job.result = {"capture_id": "player_001"}

    assert module.capture_player_view() is entrypoint.job

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


def test_callback_registration_failure_retains_unfinished_active_job(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch)
    entrypoint.job.callback_error = RuntimeError("registration failed")

    with pytest.raises(RuntimeError, match="registration failed"):
        module.capture_player_view()

    assert module._active_job is entrypoint.job
    assert messages[-1] == "Argus capture failed: registration failed"
    assert module.capture_player_view() is entrypoint.job
    assert len(entrypoint.calls) == 1


def test_capture_requires_a_pie_world(monkeypatch):
    module, entrypoint, messages = import_player_capture(monkeypatch, world=None)

    with pytest.raises(RuntimeError, match="PIE world"):
        module.capture_player_view()

    assert entrypoint.calls == []
    assert messages[-1] == "Argus capture failed: No PIE world is running"
