import json
import sys

from scripts.capture_pose_probe import _clear_argus_modules, load_probe_config


def test_load_probe_config_reads_pose_and_capture_id_prefix(tmp_path):
    path = tmp_path / "probe.json"
    path.write_text(
        json.dumps(
            {
                "capture_id_prefix": "manual_probe",
                "pose": {
                    "x": 1,
                    "y": 2,
                    "z": 3,
                    "pitch": -90,
                    "yaw": 0,
                    "roll": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = load_probe_config(path)

    assert cfg["capture_id_prefix"] == "manual_probe"
    assert cfg["pose"] == {
        "x": 1.0,
        "y": 2.0,
        "z": 3.0,
        "pitch": -90.0,
        "yaw": 0.0,
        "roll": 0.0,
    }


def test_clear_argus_modules_removes_capture_entrypoint_cache(monkeypatch):
    sentinel = object()

    for module_name in (
        "capture_rgb_and_mask",
        "common",
        "argus_components.runtime_semantics",
        "argus_core.semantics.auto_stencil",
    ):
        monkeypatch.setitem(sys.modules, module_name, sentinel)

    _clear_argus_modules()

    assert "capture_rgb_and_mask" not in sys.modules
    assert "common" not in sys.modules
    assert "argus_components.runtime_semantics" not in sys.modules
    assert "argus_core.semantics.auto_stencil" not in sys.modules
