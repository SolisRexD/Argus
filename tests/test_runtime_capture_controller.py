import importlib
import sys
import types

import pytest

from argus_core.capture import RuntimePreparationPlan


def import_runtime_control(monkeypatch):
    fake_unreal = types.SimpleNamespace(
        log=lambda message: None,
        log_warning=lambda message: None,
    )
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in (
        "common",
        "argus_backends.ue",
        "argus_backends.ue.editor",
        "argus_components.runtime_control",
    ):
        sys.modules.pop(module_name, None)
    return importlib.import_module("argus_components.runtime_control")


def test_finish_after_capture_executes_post_capture_console_commands(monkeypatch):
    module = import_runtime_control(monkeypatch)

    events = []
    controller = module.RuntimeCaptureController()
    controller._execute_console_command = lambda world, command: events.append(
        ("console", command)
    )
    controller._restore_player_streaming_source = lambda: events.append(("restore", None))
    controller.set_game_paused = lambda paused: events.append(("paused", paused))

    plan = RuntimePreparationPlan(
        enabled=True,
        pause_after_warmup=True,
        resume_after_capture=True,
        move_player_to_capture=True,
        restore_player_after_capture=True,
        post_capture_console_commands=("FastGeo.EnableTransformer 1",),
    )

    controller.finish_after_capture(plan)

    assert events == [
        ("console", "FastGeo.EnableTransformer 1"),
        ("restore", None),
        ("paused", False),
    ]


def test_finish_after_capture_does_not_restore_player_when_disabled(monkeypatch):
    module = import_runtime_control(monkeypatch)

    events = []
    controller = module.RuntimeCaptureController()
    controller._restore_player_streaming_source = lambda: events.append("restore")

    plan = RuntimePreparationPlan(
        enabled=True,
        move_player_to_capture=True,
        restore_player_after_capture=False,
    )

    controller.finish_after_capture(plan)

    assert events == []


def test_prepare_for_capture_requests_streaming_without_sleeping_or_pausing(monkeypatch):
    module = import_runtime_control(monkeypatch)
    events = []
    world = object()
    controller = module.RuntimeCaptureController()
    controller._sleep = lambda seconds: events.append(("sleep", seconds))
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
    assert not any(event[0] in {"sleep", "paused"} for event in events)


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
