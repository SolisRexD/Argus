import importlib
import sys
import types


class FakeJob:
    def __init__(self, finalize):
        self.finalize = finalize
        self.done = False
        self.result = None
        self.error = None
        self.callbacks = []

    def add_done_callback(self, callback):
        self.callbacks.append(callback)
        return self

    def finish(self, row):
        try:
            self.result = self.finalize(row)
        except Exception as exc:
            self.error = exc
        self.done = True
        for callback in self.callbacks:
            callback(self)


class FakeCaptureService:
    def __init__(self):
        self.started_ids = []
        self.jobs = []

    def capture_once(self, cfg, capture_id=None, pose=None, finalize=None):
        self.started_ids.append(capture_id)
        job = FakeJob(finalize)
        self.jobs.append(job)
        return job


def import_batch_capture(monkeypatch):
    fake_unreal = types.SimpleNamespace(
        log=lambda message: None,
        log_warning=lambda message: None,
    )
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in list(sys.modules):
        if (
            module_name == "common"
            or module_name.startswith("argus_backends.ue")
            or module_name == "argus_components"
            or module_name.startswith("argus_components.")
            or module_name == "scripts.batch_capture"
        ):
            sys.modules.pop(module_name, None)
    return importlib.import_module("scripts.batch_capture")


def test_batch_runner_starts_next_capture_only_after_completion(monkeypatch):
    module = import_batch_capture(monkeypatch)
    service = FakeCaptureService()
    completed = []
    runner = module.BatchCaptureRunner(
        capture_service=service,
        cfg={},
        items=[
            {"capture_id": "a", "pose": {"x": 1}},
            {"capture_id": "b", "pose": {"x": 2}},
        ],
        finalize_row=lambda item, row: completed.append((item["capture_id"], row))
        or row,
        continue_on_error=True,
    ).start()

    assert service.started_ids == ["a"]
    service.jobs[0].finish({"capture_id": "a"})
    assert service.started_ids == ["a", "b"]
    service.jobs[1].finish({"capture_id": "b"})

    assert runner.done is True
    assert runner.success_count == 2
    assert runner.failed_count == 0
    assert completed == [
        ("a", {"capture_id": "a"}),
        ("b", {"capture_id": "b"}),
    ]


def test_batch_runner_stops_after_error_when_continue_is_disabled(monkeypatch):
    module = import_batch_capture(monkeypatch)
    service = FakeCaptureService()
    runner = module.BatchCaptureRunner(
        capture_service=service,
        cfg={},
        items=[
            {"capture_id": "a", "pose": {}},
            {"capture_id": "b", "pose": {}},
        ],
        finalize_row=lambda item, row: (_ for _ in ()).throw(RuntimeError("bad row")),
        continue_on_error=False,
    ).start()

    service.jobs[0].finish({"capture_id": "a"})

    assert runner.done is True
    assert str(runner.error) == "bad row"
    assert runner.failed_count == 1
    assert service.started_ids == ["a"]
