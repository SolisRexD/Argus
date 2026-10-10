"""Tick-driven cleanup/generation barrier for the owned CPU PCG lab."""
import time
import uuid

import unreal
from argus_backends.ue.pcg import get_generator, inspect_instances, snapshot
from argus_core.pcg import validate_seed

_active = None
_capture_owner = None


def acquire_capture(owner):
    global _capture_owner
    if _capture_owner is not None and _capture_owner is not owner:
        raise RuntimeError("A PCG capture sequence is active")
    _capture_owner = owner


def release_capture(owner):
    global _capture_owner
    if _capture_owner is owner:
        _capture_owner = None


class PCGGenerationJob:
    def __init__(self, recipe, classes, seed):
        self.recipe, self.classes, self.seed = recipe, classes, validate_seed(seed)
        self.done, self.error, self.result = False, None, None
        self.phase = "created"
        self._bindings, self._callbacks = [], []
        self._tick_handle = None
        self._actor = self._component = None
        self._event = None

    def add_done_callback(self, callback):
        if self.done:
            callback(self)
        else:
            self._callbacks.append(callback)
        return self

    def _bind(self, name, callback):
        delegate = self._component.get_editor_property(name)
        delegate.add_callable(callback)
        self._bindings.append((delegate, callback))

    def start(self):
        global _active
        if _capture_owner is not None:
            raise RuntimeError("Finish the PCG capture sequence before regenerating")
        if _active is not None and not _active.done:
            raise RuntimeError("Another PCG generation is active")
        self._actor, self._component = get_generator(self.recipe)
        if self._component.get_editor_property("generation_in_progress"):
            raise RuntimeError("Wait for the existing PCG generation to finish")
        if unreal.GameplayStatics.is_game_paused(self._actor.get_world()):
            raise RuntimeError("Unpause before generating PCG")
        _active = self
        self._started = time.monotonic()
        try:
            self._component.component_tags = [t for t in self._component.component_tags
                                               if not str(t).startswith("ArgusGeneration:")]
            self._bind("on_pcg_graph_cleaned_external", self._cleaned)
            self._bind("on_pcg_graph_generated_external", self._generated)
            self._bind("on_pcg_graph_cancelled_external", self._cancelled)
            self._tick_handle = unreal.register_slate_post_tick_callback(self._tick)
            self.phase = "cleaning"
            if self._component.generated or self._actor.get_components_by_class(unreal.InstancedStaticMeshComponent):
                self._component.cleanup_local(True)
            else:
                # No resources: CleanupLocal intentionally emits no completion event.
                self._event = "cleaned"
        except Exception as exc:
            self._finish(exc)
        return self

    def _cleaned(self, component):
        if component == self._component and self.phase == "cleaning":
            self._event = "cleaned"

    def _generated(self, component):
        if component == self._component and self.phase == "generating":
            self._event = "generated"

    def _cancelled(self, component):
        if component == self._component:
            self._event = "cancelled"

    def _tick(self, delta):
        try:
            if time.monotonic() - self._started > self.recipe["timeout_seconds"]:
                raise TimeoutError("PCG timeout in " + self.phase + "; capture blocked, UE work may still be running")
            if self._event == "cancelled":
                raise RuntimeError("PCG generation was cancelled")
            if self.phase == "cleaning" and self._event == "cleaned":
                if inspect_instances(self._actor):
                    raise RuntimeError("PCG cleanup left live instances")
                self._event = None
                self._component.set_editor_property("seed", self.seed)
                self.phase = "generating"
                self._component.generate_local(True)
            elif self.phase == "generating" and self._event == "generated":
                generation_id = uuid.uuid4().hex
                self._component.component_tags = list(self._component.component_tags) + ["ArgusGeneration:" + generation_id]
                self.result = snapshot(self.recipe, self.classes)
                if self.result["seed"] != self.seed:
                    raise RuntimeError("PCG seed changed during generation")
                self.result["generation_id"] = generation_id
                self.result["elapsed_seconds"] = time.monotonic() - self._started
                self._finish()
        except Exception as exc:
            self._finish(exc)

    def _finish(self, error=None):
        global _active
        if self.done:
            return
        self.error = error.with_traceback(None) if error else None
        if error:
            self.result = None
        self.phase = "failed" if error else "complete"
        if self._tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self._tick_handle)
            self._tick_handle = None
        for delegate, callback in self._bindings:
            delegate.remove_callable(callback)
        self._bindings.clear()
        self._actor = self._component = None
        self.done = True
        if _active is self:
            _active = None
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback(self)
            except Exception as exc:
                unreal.log_error("PCG completion callback: " + str(exc))
        print("ARGUS_PCG_GENERATION", self.phase, str(error or ""))
