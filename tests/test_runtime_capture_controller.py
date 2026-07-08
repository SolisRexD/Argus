import importlib
import sys
import types

from argus_core.capture import RuntimePreparationPlan


def import_runtime_control(monkeypatch):
    fake_unreal = types.SimpleNamespace()
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in (
        "common",
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
