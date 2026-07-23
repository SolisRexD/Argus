import importlib
import json
import sys
import types


class FakeCaptureService:
    instance = None

    def __init__(self):
        self.finalize = None
        self.job = object()
        FakeCaptureService.instance = self

    def capture_once(self, cfg, capture_id=None, pose=None, finalize=None):
        self.finalize = finalize
        return self.job


class FakePipeline:
    appended = []

    def append_capture_metadata(self, path, row):
        self.appended.append((path, row))


def import_entrypoint(monkeypatch, cfg):
    fake_components = types.SimpleNamespace(
        CaptureService=FakeCaptureService,
        DataPipelineService=FakePipeline,
    )
    fake_common = types.SimpleNamespace(
        load_json_config=lambda path: (cfg, path),
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
