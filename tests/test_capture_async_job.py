import gc
import importlib
import sys
import types
import weakref

from argus_core.capture import RuntimePlaySessionPlan, RuntimePreparationPlan


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


def test_timeout_error_does_not_keep_finished_job_alive(monkeypatch):
    module, unreal_api = import_capture_system(monkeypatch)
    clock = FakeClock()
    job = module.CaptureJob(
        capture_id="timeout-lifetime",
        runtime_plan=RuntimePreparationPlan(
            enabled=True,
            wait_for_streaming=True,
            streaming_timeout_seconds=1.0,
        ),
        is_streaming_completed=lambda: False,
        prepare_semantics=lambda: {},
        capture=lambda stats: {},
        cleanup=lambda: None,
        clock=clock,
    ).start()
    job_ref = weakref.ref(job)

    clock.advance(2.0)
    unreal_api.tick()
    error = job.error

    gc.disable()
    try:
        job = None
        assert job_ref() is None
    finally:
        gc.enable()

    assert isinstance(error, TimeoutError)


def test_capture_service_applies_semantics_only_after_streaming(monkeypatch, tmp_path):
    module, unreal_api = import_capture_system(monkeypatch)
    stream = module.CaptureStreamSpec(
        name="mask",
        actor_label="SC_MASK",
        rt_asset_name="RT_MASK",
        file_suffix="mask",
        apply_post_process=False,
        post_process_material_name="",
        sync_to_primary=False,
        force_png_opaque=False,
        capture_source="SCS_FINAL_COLOR_LDR",
    )

    class FakeRegistry:
        def __init__(self, cfg):
            self.cfg = cfg

        def list_streams(self):
            return [stream]

        def get_primary_stream(self, streams):
            return streams[0]

    actor = types.SimpleNamespace(
        get_actor_location=lambda: types.SimpleNamespace(x=1.0, y=2.0, z=3.0),
        get_actor_rotation=lambda: types.SimpleNamespace(pitch=4.0, yaw=5.0, roll=6.0),
    )
    component = object()
    semantic_calls = []
    capture_events = []
    readiness = iter([True, True, True])
    runtime_plan = RuntimePreparationPlan(
        enabled=True,
        warmup_seconds=0.0,
        wait_for_streaming=True,
        streaming_timeout_seconds=10.0,
    )
    streaming_source = types.SimpleNamespace(
        is_streaming_completed=lambda: next(readiness)
    )
    runtime_controller = types.SimpleNamespace(
        prepare_for_capture=lambda cfg, pose, capture_actor: runtime_plan,
        make_streaming_query_source=lambda capture_actor: streaming_source,
        set_game_paused=lambda paused: None,
        finish_after_capture=lambda plan: capture_events.append("cleanup"),
    )
    semantic_controller = types.SimpleNamespace(
        apply=lambda cfg, pose: semantic_calls.append("semantics") or {"scanned": 10}
    )
    service = module.CaptureService()
    service.runtime_session_controller = types.SimpleNamespace(
        validate_capture_session=lambda cfg: RuntimePlaySessionPlan(enabled=False)
    )
    service.runtime_controller = runtime_controller
    service.semantic_stencil_controller = semantic_controller
    service.intrinsics_manager = types.SimpleNamespace(
        resolve_intrinsics=lambda capture_cfg, pose, rt: {},
        apply_intrinsics=lambda component, intrinsics: None,
        intrinsics_to_metadata=lambda component, intrinsics: {},
    )
    service._configure_component = lambda *args, **kwargs: None
    service._configure_stream_post_process = lambda *args, **kwargs: None
    service._capture_twice = lambda current_component: capture_events.append("capture")
    service._choose_ext_by_rt = lambda rt: ".png"
    service._export_rt = lambda rt, path: None

    monkeypatch.setattr(module, "CaptureStreamRegistry", FakeRegistry)
    monkeypatch.setattr(module, "find_actor_by_label", lambda label: actor)
    monkeypatch.setattr(module, "get_capture_component", lambda current_actor: component)
    monkeypatch.setattr(module, "load_asset_or_raise", lambda path: object())

    cfg = {
        "assets": {"root": "/Game/Tools/Semantic"},
        "capture": {},
        "output": {"capture_dir": str(tmp_path), "file_prefix": "cap"},
        "batch": {"sleep_seconds": 0.0},
    }

    job = service.capture_once(cfg, capture_id="first")

    assert isinstance(job, module.CaptureJob)
    assert semantic_calls == []
    assert capture_events == []

    unreal_api.tick()
    unreal_api.tick()
    unreal_api.tick()
    assert semantic_calls == ["semantics"]
    assert capture_events == []

    unreal_api.tick()
    assert job.done is True
    assert job.result["capture_id"] == "first"
    assert capture_events == ["capture", "cleanup"]
