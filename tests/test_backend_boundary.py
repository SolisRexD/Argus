import importlib
import sys
import types


def test_common_is_a_compatibility_facade_for_core_and_ue_helpers(monkeypatch):
    monkeypatch.syspath_prepend("scripts")
    fake_unreal = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)

    for module_name in (
        "common",
        "argus_backends.ue",
        "argus_backends.ue.editor",
    ):
        sys.modules.pop(module_name, None)

    core_io = importlib.import_module("argus_core.io")
    ue_editor = importlib.import_module("argus_backends.ue.editor")
    common = importlib.import_module("common")

    assert common.parse_bool is core_io.parse_bool
    assert common.read_pose_rows is core_io.read_pose_rows
    assert common.find_actor_by_label is ue_editor.find_actor_by_label
    assert common.load_asset_or_raise is ue_editor.load_asset_or_raise
    assert ue_editor.unreal is fake_unreal
