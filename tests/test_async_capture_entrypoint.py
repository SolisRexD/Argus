import importlib
import json
import sys
import types


config_load_calls = []


class FakeCaptureService:
    instance = None

    def __init__(self):
        self.finalize = None
        self.job = object()
        FakeCaptureService.instance = self

    def capture_once(self, cfg, capture_id=None, pose=None, finalize=None):
        self.cfg = cfg
        self.capture_id = capture_id
        self.pose = pose
        self.finalize = finalize
        return self.job


class FakePipeline:
    appended = []

    def append_capture_metadata(self, path, row):
        self.appended.append((path, row))


def import_entrypoint(monkeypatch, cfg):
    config_load_calls.clear()

    def load_json_config(path):
        config_load_calls.append(path)
        return cfg, path

    fake_components = types.SimpleNamespace(
        CaptureService=FakeCaptureService,
        DataPipelineService=FakePipeline,
    )
    fake_common = types.SimpleNamespace(
        load_json_config=load_json_config,
        log=lambda message: None,
        resolve_path=lambda path: path,
    )
    monkeypatch.setitem(sys.modules, "argus_components", fake_components)
    monkeypatch.setitem(sys.modules, "common", fake_common)
    sys.modules.pop("scripts.capture_rgb_and_mask", None)
    return importlib.import_module("scripts.capture_rgb_and_mask")


def test_single_capture_entrypoint_returns_job_and_finalizes_metadata(
    monkeypatch,
    tmp_path,
):
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

    job = module.capture_once(capture_id="one")

    assert job is FakeCaptureService.instance.job
    row = {
        "capture_id": "one",
        "files_json": json.dumps(
            {"rgb": str(rgb_path), "mask": str(mask_path)},
            ensure_ascii=False,
        ),
    }
    assert FakeCaptureService.instance.finalize(row) is row
    assert FakePipeline.appended == [(metadata_path, row)]


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
    pose = object()

    job = module.capture_with_config(cfg, capture_id="configured", pose=pose)

    assert job is FakeCaptureService.instance.job
    assert FakeCaptureService.instance.cfg is cfg
    assert FakeCaptureService.instance.capture_id == "configured"
    assert FakeCaptureService.instance.pose is pose
    assert config_load_calls == []
    row = {
        "capture_id": "configured",
        "files_json": json.dumps(
            {"rgb": str(rgb_path), "mask": str(mask_path)},
            ensure_ascii=False,
        ),
    }
    assert FakeCaptureService.instance.finalize(row) is row
    assert FakePipeline.appended == [(metadata_path, row)]
