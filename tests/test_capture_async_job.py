import importlib
import sys

from argus_core.capture import RuntimePreparationPlan


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class FakeUnreal:
    def __init__(self):
        self.callbacks = {}
        self.next_handle = 1
        self.unregister_count = 0

    def log(self, message):
        pass

    def log_warning(self, message):
        pass

    def register_slate_post_tick_callback(self, callback):
        handle = self.next_handle
        self.next_handle += 1
        self.callbacks[handle] = callback
        return handle

    def unregister_slate_post_tick_callback(self, handle):
        self.callbacks.pop(handle, None)
        self.unregister_count += 1

    def tick(self):
        for callback in list(self.callbacks.values()):
            callback(1.0 / 60.0)


def import_capture_system(monkeypatch):
    fake_unreal = FakeUnreal()
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in list(sys.modules):
        if (
            module_name == "common"
            or module_name.startswith("argus_backends.ue")
            or module_name == "argus_components"
            or module_name.startswith("argus_components.")
        ):
            sys.modules.pop(module_name, None)
    module = importlib.import_module("argus_components.capture_system")
    return module, fake_unreal


def test_job_waits_for_two_streaming_ticks_then_warmup_and_capture(monkeypatch):
    module, unreal_api = import_capture_system(monkeypatch)
    clock = FakeClock()
    events = []
    readiness = iter([False, True, True, True])
    plan = RuntimePreparationPlan(
        enabled=True,
        warmup_seconds=2.0,
        wait_for_streaming=True,
        streaming_timeout_seconds=10.0,
    )
    job = module.CaptureJob(
        capture_id="harbor",
        runtime_plan=plan,
        is_streaming_completed=lambda: next(readiness),
        prepare_semantics=lambda: events.append("semantics") or {"scanned": 29329},
        capture=lambda stats: events.append(("capture", stats))
        or {"capture_id": "harbor"},
        cleanup=lambda: events.append("cleanup"),
        clock=clock,
    ).start()

    unreal_api.tick()
    unreal_api.tick()
    unreal_api.tick()
    assert events == []

    clock.advance(2.0)
    unreal_api.tick()
    assert events == ["semantics"]
    assert job.done is False

    unreal_api.tick()
    assert job.done is True
    assert job.result == {"capture_id": "harbor"}
    assert events == [
        "semantics",
        ("capture", {"scanned": 29329}),
        "cleanup",
    ]
    assert unreal_api.unregister_count == 1


def test_job_resets_warmup_when_streaming_regresses(monkeypatch):
    module, unreal_api = import_capture_system(monkeypatch)
    clock = FakeClock()
    readiness = iter([True, True, False, True, True, True])
    events = []
    plan = RuntimePreparationPlan(
        enabled=True,
        warmup_seconds=1.0,
        wait_for_streaming=True,
        streaming_timeout_seconds=10.0,
    )
    job = module.CaptureJob(
        capture_id="retry",
        runtime_plan=plan,
        is_streaming_completed=lambda: next(readiness),
        prepare_semantics=lambda: events.append("semantics") or {},
        capture=lambda stats: {},
        cleanup=lambda: None,
        clock=clock,
    ).start()

    unreal_api.tick()
    unreal_api.tick()
    clock.advance(1.0)
    unreal_api.tick()
    assert events == []

    unreal_api.tick()
    unreal_api.tick()
    clock.advance(1.0)
    unreal_api.tick()
    assert events == ["semantics"]
    assert job.done is False


def test_job_timeout_and_capture_error_both_cleanup(monkeypatch):
    module, unreal_api = import_capture_system(monkeypatch)
    clock = FakeClock()
    cleanup_events = []
    plan = RuntimePreparationPlan(
        enabled=True,
        wait_for_streaming=True,
        streaming_timeout_seconds=1.0,
    )
    timeout_job = module.CaptureJob(
        capture_id="timeout",
        runtime_plan=plan,
        is_streaming_completed=lambda: False,
        prepare_semantics=lambda: {},
        capture=lambda stats: {},
        cleanup=lambda: cleanup_events.append("timeout"),
        clock=clock,
    ).start()
    clock.advance(2.0)
    unreal_api.tick()

    assert timeout_job.done is True
    assert isinstance(timeout_job.error, TimeoutError)
    assert cleanup_events == ["timeout"]

    error_job = module.CaptureJob(
        capture_id="error",
        runtime_plan=RuntimePreparationPlan(enabled=False),
        is_streaming_completed=lambda: True,
        prepare_semantics=lambda: {},
        capture=lambda stats: (_ for _ in ()).throw(RuntimeError("capture failed")),
        cleanup=lambda: cleanup_events.append("error"),
        clock=clock,
    ).start()
    unreal_api.tick()
    unreal_api.tick()
    unreal_api.tick()

    assert str(error_job.error) == "capture failed"
    assert cleanup_events == ["timeout", "error"]
    assert unreal_api.unregister_count == 2
