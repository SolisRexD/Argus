import importlib
import sys
import types

import pytest


class FakeWeightedBlendables:
    def __init__(self):
        self.array = ["stale"]


class FakePostProcessSettings:
    def __init__(self):
        self.weighted_blendables = FakeWeightedBlendables()

    def get_editor_property(self, name):
        return getattr(self, name)

    def set_editor_property(self, name, value):
        setattr(self, name, value)


class FakeComponent:
    def __init__(self):
        self.post_process_settings = FakePostProcessSettings()
        self.post_process_blend_weight = None

    def get_editor_property(self, name):
        return getattr(self, name)

    def set_editor_property(self, name, value):
        setattr(self, name, value)


class FakeWeightedBlendable:
    def __init__(self):
        self.object = None
        self.weight = 0.0


def import_capture_system(monkeypatch):
    fake_unreal = types.SimpleNamespace(WeightedBlendable=FakeWeightedBlendable)
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in (
        "common",
        "argus_components",
        "argus_components.capture_system",
    ):
        sys.modules.pop(module_name, None)
    return importlib.import_module("argus_components.capture_system")


def test_capture_service_reapplies_stream_post_process_material(monkeypatch):
    module = import_capture_system(monkeypatch)
    component = FakeComponent()
    material = object()
    loaded_paths = []

    def load_asset(path):
        loaded_paths.append(path)
        return material

    monkeypatch.setattr(module, "load_asset_or_raise", load_asset)
    stream = module.CaptureStreamSpec(
        name="mask",
        actor_label="SC_MASK",
        rt_asset_name="RT_MASK",
        file_suffix="mask",
        apply_post_process=True,
        post_process_material_name="M_PP_SemanticMask_Auto",
        sync_to_primary=True,
        force_png_opaque=True,
        capture_source="SCS_FINAL_COLOR_LDR",
    )

    module.CaptureService()._configure_stream_post_process(
        component,
        stream,
        {
            "root": "/Game/Tools/Semantic",
            "material_name": "M_PP_SemanticMask_Auto",
        },
        {},
    )

    weighted = component.post_process_settings.weighted_blendables
    assert loaded_paths == [
        "/Game/Tools/Semantic/M_PP_SemanticMask_Auto",
    ]
    assert component.post_process_blend_weight == 1.0
    assert len(weighted.array) == 1
    assert weighted.array[0].object is material
    assert weighted.array[0].weight == 1.0


def test_setup_and_capture_share_base_post_process_implementation(monkeypatch):
    module = import_capture_system(monkeypatch)

    assert (
        module.DualCaptureSetupService._configure_stream_post_process
        is module.BaseCaptureService._configure_stream_post_process
    )
    assert (
        module.CaptureService._configure_stream_post_process
        is module.BaseCaptureService._configure_stream_post_process
    )


def test_capture_once_validates_session_and_rebinds_post_process_each_time(
    monkeypatch,
    tmp_path,
):
    module = import_capture_system(monkeypatch)
    stream = module.CaptureStreamSpec(
        name="mask",
        actor_label="SC_MASK",
        rt_asset_name="RT_MASK",
        file_suffix="mask",
        apply_post_process=True,
        post_process_material_name="M_PP_SemanticMask_Auto",
        sync_to_primary=True,
        force_png_opaque=True,
        capture_source="SCS_FINAL_COLOR_LDR",
    )

    class FakeRegistry:
        def __init__(self, cfg):
            self.cfg = cfg

        def list_streams(self):
            return [stream]

        def get_primary_stream(self, streams):
            return streams[0]

    class StopAfterPostProcess(Exception):
        pass

    events = []
    service = module.CaptureService()
    service.runtime_session_controller = types.SimpleNamespace(
        validate_capture_session=lambda cfg: events.append("validate")
    )
    service._configure_component = lambda *args, **kwargs: None
    service._configure_stream_post_process = (
        lambda *args, **kwargs: events.append("post_process")
    )
    service.intrinsics_manager = types.SimpleNamespace(
        resolve_intrinsics=lambda *args, **kwargs: (_ for _ in ()).throw(
            StopAfterPostProcess()
        )
    )

    monkeypatch.setattr(module, "CaptureStreamRegistry", FakeRegistry)
    monkeypatch.setattr(module, "find_actor_by_label", lambda label: object())
    monkeypatch.setattr(module, "get_capture_component", lambda actor: FakeComponent())
    monkeypatch.setattr(module, "load_asset_or_raise", lambda path: object())

    cfg = {
        "assets": {"root": "/Game/Tools/Semantic"},
        "capture": {},
        "output": {"capture_dir": str(tmp_path)},
    }

    for _ in range(2):
        with pytest.raises(StopAfterPostProcess):
            service.capture_once(cfg)

    assert events == [
        "validate",
        "post_process",
        "validate",
        "post_process",
    ]
