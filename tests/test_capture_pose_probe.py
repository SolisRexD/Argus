import json
import sys
import types

import scripts.capture_pose_probe as probe_module
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


def test_probe_prints_result_only_after_capture_job_finishes(monkeypatch, capsys):
    class FakeJob:
        def __init__(self):
            self.result = None
            self.error = None
            self.callback = None

        def add_done_callback(self, callback):
            self.callback = callback
            return self

        def finish(self, row):
            self.result = row
            self.callback(self)

    job = FakeJob()
    fake_capture_module = types.SimpleNamespace(
        capture_once=lambda capture_id, pose: job
    )
    monkeypatch.setitem(sys.modules, "capture_rgb_and_mask", fake_capture_module)
    monkeypatch.setattr(probe_module, "_prepare_import_paths", lambda: None)
    monkeypatch.setattr(probe_module, "_clear_argus_modules", lambda: None)
    monkeypatch.setattr(
        probe_module,
        "load_probe_config",
        lambda path=None: {
            "capture_id_prefix": "probe",
            "pose": {"x": 1.0, "y": 2.0, "z": 3.0, "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        },
    )

    returned = probe_module.main()

    assert returned is job
    assert "ARGUS_PROBE_RESULT" not in capsys.readouterr().out

    job.finish(
        {
            "capture_id": "probe_1",
            "files_json": json.dumps({"rgb": "rgb.png", "mask": "mask.png"}),
        }
    )
    output = capsys.readouterr().out
    assert "ARGUS_PROBE_RESULT=" in output
    assert '"capture_id": "probe_1"' in output
    assert '"mask_file": "mask.png"' in output
