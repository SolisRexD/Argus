import importlib
import json
import sys
import types

import pytest


def citysample_config():
    return {
        "runtime": {
            "enabled": True,
            "profile": "citysample_bigcity",
            "citysample": {
                "disable_fastgeo_transformer_before_play": True,
                "restore_fastgeo_transformer_after_play": True,
            },
        }
    }


def import_runtime_session(
    monkeypatch,
    tmp_path,
    *,
    initial_fastgeo=True,
    game_world=None,
    editor_world="editor_world",
    pid=1234,
    execute_error=None,
    execute_error_on=None,
):
    values = {"FastGeo.EnableTransformer": bool(initial_fastgeo)}
    events = []
    worlds = {"game": game_world, "editor": editor_world}

    class FakeSystemLibrary:
        @staticmethod
        def get_console_variable_bool_value(name):
            return values[name]

        @staticmethod
        def execute_console_command(world, command):
            if execute_error and (execute_error_on is None or command == execute_error_on):
                raise execute_error

            events.append((world, command))
            name, raw_value = command.split(None, 1)
            if name == "FastGeo.EnableTransformer":
                values[name] = raw_value.strip() not in ("0", "false", "False")

    fake_unreal = types.SimpleNamespace(
        SystemLibrary=FakeSystemLibrary,
        log=lambda message: None,
        log_warning=lambda message: None,
    )
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in (
        "common",
        "argus_components.runtime_session",
    ):
        sys.modules.pop(module_name, None)

    module = importlib.import_module("argus_components.runtime_session")
    state_path = tmp_path / "runtime_play_session_state.json"
    controller = module.RuntimePlaySessionController(
        state_path=str(state_path),
        pid_getter=lambda: pid,
    )
    controller._get_game_world = lambda: worlds["game"]
    controller._get_editor_world = lambda: worlds["editor"]
    return controller, values, events, worlds, state_path


def test_prepare_and_restore_preserve_original_fastgeo_value(monkeypatch, tmp_path):
    controller, values, events, worlds, state_path = import_runtime_session(
        monkeypatch,
        tmp_path,
        initial_fastgeo=True,
    )

    controller.prepare_before_play(citysample_config())

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert values["FastGeo.EnableTransformer"] is False
    assert state["editor_pid"] == 1234
    assert state["original_console_values"]["FastGeo.EnableTransformer"] is True

    worlds["game"] = "pie_world"
    controller.validate_capture_session(citysample_config())

    worlds["game"] = None
    controller.restore_after_play(citysample_config())

    assert values["FastGeo.EnableTransformer"] is True
    assert events == [
        ("editor_world", "FastGeo.EnableTransformer 0"),
        ("editor_world", "FastGeo.EnableTransformer 1"),
    ]
    assert not state_path.exists()


def test_restore_keeps_fastgeo_disabled_when_it_was_originally_disabled(
    monkeypatch,
    tmp_path,
):
    controller, values, events, _, _ = import_runtime_session(
        monkeypatch,
        tmp_path,
        initial_fastgeo=False,
    )

    controller.prepare_before_play(citysample_config())
    controller.restore_after_play(citysample_config())

    assert values["FastGeo.EnableTransformer"] is False
    assert events == [
        ("editor_world", "FastGeo.EnableTransformer 0"),
        ("editor_world", "FastGeo.EnableTransformer 0"),
    ]


def test_restore_uses_prepared_state_when_runtime_config_changes(monkeypatch, tmp_path):
    controller, values, events, _, state_path = import_runtime_session(
        monkeypatch,
        tmp_path,
    )
    controller.prepare_before_play(citysample_config())

    controller.restore_after_play({"runtime": {"enabled": False}})

    assert values["FastGeo.EnableTransformer"] is True
    assert events == [
        ("editor_world", "FastGeo.EnableTransformer 0"),
        ("editor_world", "FastGeo.EnableTransformer 1"),
    ]
    assert not state_path.exists()


def test_restore_can_intentionally_leave_fastgeo_disabled(monkeypatch, tmp_path):
    controller, values, events, _, state_path = import_runtime_session(
        monkeypatch,
        tmp_path,
    )
    cfg = citysample_config()
    cfg["runtime"]["citysample"]["restore_fastgeo_transformer_after_play"] = False

    controller.prepare_before_play(cfg)
    controller.restore_after_play(cfg)

    assert values["FastGeo.EnableTransformer"] is False
    assert events == [("editor_world", "FastGeo.EnableTransformer 0")]
    assert not state_path.exists()


def test_prepare_rejects_running_pie(monkeypatch, tmp_path):
    controller, _, _, _, _ = import_runtime_session(
        monkeypatch,
        tmp_path,
        game_world="pie_world",
    )

    with pytest.raises(RuntimeError, match="before PIE"):
        controller.prepare_before_play(citysample_config())


def test_restore_rejects_running_pie(monkeypatch, tmp_path):
    controller, _, _, worlds, _ = import_runtime_session(monkeypatch, tmp_path)
    controller.prepare_before_play(citysample_config())
    worlds["game"] = "pie_world"

    with pytest.raises(RuntimeError, match="after PIE"):
        controller.restore_after_play(citysample_config())


def test_prepare_fails_when_editor_world_is_missing(monkeypatch, tmp_path):
    controller, _, _, _, state_path = import_runtime_session(
        monkeypatch,
        tmp_path,
        editor_world=None,
    )

    with pytest.raises(RuntimeError, match="editor world"):
        controller.prepare_before_play(citysample_config())

    assert not state_path.exists()


def test_prepare_propagates_console_command_failure(monkeypatch, tmp_path):
    controller, _, _, _, state_path = import_runtime_session(
        monkeypatch,
        tmp_path,
        execute_error=RuntimeError("console failed"),
    )

    with pytest.raises(RuntimeError, match="console failed"):
        controller.prepare_before_play(citysample_config())

    assert not state_path.exists()


def test_prepare_rolls_back_fastgeo_when_a_later_command_fails(
    monkeypatch,
    tmp_path,
):
    controller, values, events, _, state_path = import_runtime_session(
        monkeypatch,
        tmp_path,
        execute_error=RuntimeError("console failed"),
        execute_error_on="r.Streaming.PoolSize 4096",
    )
    cfg = citysample_config()
    cfg["runtime"]["pre_play_console_commands"] = ["r.Streaming.PoolSize 4096"]

    with pytest.raises(RuntimeError, match="console failed"):
        controller.prepare_before_play(cfg)

    assert values["FastGeo.EnableTransformer"] is True
    assert events == [
        ("editor_world", "FastGeo.EnableTransformer 0"),
        ("editor_world", "FastGeo.EnableTransformer 1"),
    ]
    assert not state_path.exists()


def test_capture_validation_requires_matching_prepared_session(monkeypatch, tmp_path):
    controller, _, _, worlds, _ = import_runtime_session(monkeypatch, tmp_path)
    worlds["game"] = "pie_world"

    with pytest.raises(RuntimeError, match="prepare_runtime_play_session"):
        controller.validate_capture_session(citysample_config())


def test_capture_validation_rejects_fastgeo_drift(monkeypatch, tmp_path):
    controller, values, _, worlds, _ = import_runtime_session(monkeypatch, tmp_path)
    controller.prepare_before_play(citysample_config())
    values["FastGeo.EnableTransformer"] = True
    worlds["game"] = "pie_world"

    with pytest.raises(RuntimeError, match="was not applied"):
        controller.validate_capture_session(citysample_config())


def test_capture_validation_rejects_state_from_another_editor_process(
    monkeypatch,
    tmp_path,
):
    controller, _, _, worlds, _ = import_runtime_session(monkeypatch, tmp_path)
    controller.prepare_before_play(citysample_config())
    controller._pid_getter = lambda: 9876
    worlds["game"] = "pie_world"

    with pytest.raises(RuntimeError, match="different editor process"):
        controller.validate_capture_session(citysample_config())


def test_restore_failure_keeps_state_for_retry(monkeypatch, tmp_path):
    controller, _, _, _, state_path = import_runtime_session(monkeypatch, tmp_path)
    controller.prepare_before_play(citysample_config())
    controller._execute_console_command = lambda world, command: (_ for _ in ()).throw(
        RuntimeError("restore failed")
    )

    with pytest.raises(RuntimeError, match="restore failed"):
        controller.restore_after_play(citysample_config())

    assert state_path.exists()


def test_restore_requires_matching_prepared_session(monkeypatch, tmp_path):
    controller, _, _, _, _ = import_runtime_session(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="prepared session"):
        controller.restore_after_play(citysample_config())
