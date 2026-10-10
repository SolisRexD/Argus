import importlib.util
import sys
import types
from pathlib import Path

import pytest

from argus_core.dataset import sha256_file


@pytest.fixture
def runner(monkeypatch, tmp_path):
    level = types.SimpleNamespace()
    fake = types.SimpleNamespace(get_editor_subsystem=lambda cls: level,
        LevelEditorSubsystem=object,
        EditorAssetLibrary=types.SimpleNamespace(does_asset_exist=lambda path: False))
    build = types.SimpleNamespace(create=lambda *a, **kw: None)
    capture = types.SimpleNamespace(run=lambda *a, **kw: types.SimpleNamespace(add_done_callback=lambda cb: None))
    for name, value in {"unreal": fake, "build_pcg_scene": build, "capture_pcg_scene": capture,
        "argus_backends.ue.dataset_scene": types.SimpleNamespace(DatasetSceneBuilder=lambda *a: object(), validate_camera=lambda p: None),
        "argus_components.pcg_generation": types.SimpleNamespace(ensure_idle=lambda: None),
        "plan_dataset": types.SimpleNamespace(load_plan=lambda p: {})}.items():
        monkeypatch.setitem(sys.modules, name, value)
    path = Path(__file__).resolve().parents[1] / "scripts/capture_dataset.py"
    spec = importlib.util.spec_from_file_location("isolated_dataset_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    job = module.DatasetRun.__new__(module.DatasetRun)
    job.done, job.error, job.phase = False, None, "next_scene"
    job.index = job.success_count = job.skipped_count = 0
    job.directory = tmp_path
    job.plan = {"classes": {}, "scenes": [{"key": "scene1", "group": "group1", "pcg_seed": 1,
        "recipe": {"map_path": "/Game/test", "environment": {}}, "poses": [{}]}]}
    job.state = {"scenes": {}, "assets": {}}
    job._write = lambda: None
    job._check_editor = lambda: None
    job._asset_hashes = lambda scene: {}
    return job, build


def test_editor_asset_operations_cannot_reenter_batch_tick(runner):
    job, build = runner
    calls = []

    def create(*args, **kwargs):
        calls.append("create")
        # Asset creation/save can pump Slate while the outer Python call runs.
        job._tick(0.01)

    build.create = create
    job._tick(0.01)
    assert calls == ["create"]
    assert job.phase == "capturing:scene1"
    assert not job.done


def test_resume_skips_intact_artifacts_and_recaptures_corruption(runner):
    job, build = runner
    folder = job.directory / "old_attempt"
    folder.mkdir()
    frame = folder / "image.png"
    frame.write_bytes(b"original")
    job.state["scenes"]["scene1"] = {"phase": "captured", "attempt": "old_attempt",
                                      "artifacts": {"image.png": sha256_file(frame)}}
    calls = []
    build.create = lambda *a, **kw: calls.append("create")
    job._tick(0.01)
    assert job.skipped_count == 1 and not calls
    frame.write_bytes(b"corrupted")
    job.index = 0
    job._tick(0.01)
    assert calls == ["create"]
    assert job.state["scenes"]["scene1"]["attempt"] != "old_attempt"
    assert frame.read_bytes() == b"corrupted"  # Preserve the failed attempt.
