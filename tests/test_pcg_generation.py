"""Exercise delayed UE events, failures and cleanup without running the editor."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest


class Delegate:
    def __init__(self):
        self.callbacks = []

    def add_callable(self, callback):
        self.callbacks.append(callback)

    def remove_callable(self, callback):
        self.callbacks.remove(callback)

    def emit(self, component):
        for callback in list(self.callbacks):
            callback(component)


@pytest.fixture
def system(monkeypatch):
    clock = [0.0]
    tick = {}
    events = {name: Delegate() for name in ("cleaned", "generated", "cancelled")}
    calls = []
    component = types.SimpleNamespace(generated=True, seed=42, component_tags=["ArgusGeneration:old"])
    component.get_editor_property = lambda name: False if name == "generation_in_progress" else events[name.split("_")[-2]]
    component.set_editor_property = lambda name, value: setattr(component, name, value)
    component.cleanup_local = lambda flag: calls.append("cleanup")
    component.generate_local = lambda flag: calls.append("generate")
    actor = types.SimpleNamespace(get_world=lambda: "world", get_components_by_class=lambda cls: [])
    def register(callback):
        tick[1] = callback
        return 1
    fake = types.SimpleNamespace(
        InstancedStaticMeshComponent=object,
        GameplayStatics=types.SimpleNamespace(is_game_paused=lambda w: False),
        register_slate_post_tick_callback=register,
        unregister_slate_post_tick_callback=lambda handle: tick.pop(handle),
        log_error=lambda msg: None,
    )
    backend = types.ModuleType("argus_backends.ue.pcg")
    backend.get_generator = lambda recipe: (actor, component)
    backend.inspect_instances = lambda a: []
    backend.snapshot = lambda r, c: {"seed": component.seed, "scene_sha256": "verified"}
    monkeypatch.setitem(sys.modules, "unreal", fake)
    monkeypatch.setitem(sys.modules, "argus_backends.ue.pcg", backend)
    path = Path(__file__).resolve().parents[1] / "scripts/argus_components/pcg_generation.py"
    spec = importlib.util.spec_from_file_location("isolated_pcg_generation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    return types.SimpleNamespace(module=module, clock=clock, tick=tick, events=events,
                                 component=component, actor=actor, calls=calls)


def start(s):
    return s.module.PCGGenerationJob({"timeout_seconds": 10}, {}, 43).start()


def test_cleanup_barrier_requires_current_completion_event(system):
    s = system
    job = start(s)
    assert not s.component.component_tags  # The old generation proof is invalid immediately.
    s.tick[1](0)
    assert s.calls == ["cleanup"] and not job.done
    s.events["generated"].emit(s.component)  # Stale event while cleaning is ignored.
    s.events["cleaned"].emit(object())  # Another component cannot release the barrier.
    s.tick[1](0)
    assert s.calls == ["cleanup"]
    s.events["cleaned"].emit(s.component)
    assert s.calls == ["cleanup"]  # Generation waits for next tick.
    s.tick[1](0)
    assert s.calls == ["cleanup", "generate"]
    assert s.component.seed == 43 and not job.done
    s.events["generated"].emit(s.component)
    s.tick[1](0)
    assert job.done and job.error is None and job.result["seed"] == 43
    assert s.component.component_tags == ["ArgusGeneration:" + job.result["generation_id"]]
    assert not s.tick and not any(d.callbacks for d in s.events.values())
    assert job._actor is None and job._component is None


def test_first_generation_does_not_wait_for_nonexistent_cleanup_event(system):
    s = system
    s.component.generated = False
    job = start(s)
    s.tick[1](0)
    assert s.calls == ["generate"] and not job.done


@pytest.mark.parametrize("failure", ["timeout", "cancelled", "bad_output"])
def test_failure_never_releases_success_result(system, failure):
    s = system
    job = start(s)
    notified = []
    job.add_done_callback(lambda result: notified.append(result.error))
    if failure == "timeout":
        s.clock[0] = 11
    elif failure == "cancelled":
        s.events["cancelled"].emit(s.component)
    else:
        s.events["cleaned"].emit(s.component)
        s.tick[1](0)
        s.module.snapshot = lambda *args: (_ for _ in ()).throw(ValueError("wrong stencil"))
        s.events["generated"].emit(s.component)
    s.tick[1](0)
    assert job.done and job.error is not None and job.result is None
    assert notified == [job.error]
    assert not s.tick and not any(d.callbacks for d in s.events.values())


def test_generation_and_capture_are_mutually_exclusive(system):
    s = system
    owner = object()
    s.module.acquire_capture(owner)
    with pytest.raises(RuntimeError, match="capture sequence"):
        start(s)
    s.module.release_capture(object())
    assert s.module._capture_owner is owner
    s.module.release_capture(owner)
    first = start(s)
    with pytest.raises(RuntimeError, match="generation is active"):
        start(s)
    assert not first.done
