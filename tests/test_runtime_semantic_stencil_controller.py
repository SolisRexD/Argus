import importlib
import sys
import types


class FakeMesh:
    def __init__(self, name, path):
        self._name = name
        self._path = path

    def get_name(self):
        return self._name

    def get_path_name(self):
        return self._path


class FakeVector:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class FakeComponent:
    def __init__(
        self,
        name,
        class_name,
        mesh=None,
        stencil=0,
        render_custom_depth=None,
        location=None,
    ):
        self._name = name
        self._class_name = class_name
        self._mesh = mesh
        self._location = location
        self.props = {
            "render_custom_depth": bool(stencil)
            if render_custom_depth is None
            else bool(render_custom_depth),
            "custom_depth_stencil_value": stencil,
        }

    def get_name(self):
        return self._name

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)

    def get_editor_property(self, name):
        if name == "static_mesh":
            return self._mesh
        return self.props[name]

    def set_editor_property(self, name, value):
        self.props[name] = value

    def get_materials(self):
        return []

    def get_component_location(self):
        if self._location is None:
            raise RuntimeError("component location is unavailable")

        return self._location


class FakeActor:
    def __init__(self, label, class_name, components, location=None):
        self._label = label
        self._class_name = class_name
        self._components = components
        self._location = location

    def get_actor_label(self):
        return self._label

    def get_name(self):
        return self._label

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)

    def get_components_by_class(self, component_class):
        return list(self._components)

    def get_actor_location(self):
        if self._location is None:
            raise RuntimeError("actor location is unavailable")

        return self._location


def import_runtime_semantics(monkeypatch):
    fake_unreal = types.SimpleNamespace(PrimitiveComponent=object)
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in ("common", "argus_components.runtime_semantics"):
        sys.modules.pop(module_name, None)
    return importlib.import_module("argus_components.runtime_semantics")


def test_runtime_semantic_stencil_applies_vehicle_stencil_and_preserves_existing(monkeypatch):
    module = import_runtime_semantics(monkeypatch)

    vehicle_component = FakeComponent(
        "VehicleMesh",
        "StaticMeshComponent",
        mesh=FakeMesh("SM_car_body", "/Game/Vehicles/SM_car_body"),
    )
    preserved_component = FakeComponent(
        "RoadMesh",
        "StaticMeshComponent",
        mesh=FakeMesh("SM_road", "/Game/Road/SM_road"),
        stencil=2,
    )
    actor = FakeActor(
        "BP_vehCar_vehicle07_Sandbox_C",
        "BP_vehCar_vehicle07_Sandbox_C",
        [vehicle_component, preserved_component],
    )

    controller = module.RuntimeSemanticStencilController(
        actor_provider=lambda: [actor],
        log_fn=lambda msg: None,
        warn_fn=lambda msg: None,
    )

    stats = controller.apply(
        {
            "runtime": {
                "auto_semantic_stencil": {
                    "enabled": True,
                    "preserve_existing": True,
                    "unknown_for_unmatched": True,
                }
            }
        }
    )

    assert stats["changed"] == 1
    assert stats["preserved"] == 1
    assert vehicle_component.props["render_custom_depth"] is True
    assert vehicle_component.props["custom_depth_stencil_value"] == 6
    assert preserved_component.props["custom_depth_stencil_value"] == 2


def test_runtime_semantic_stencil_enables_custom_depth_for_preserved_stencil(monkeypatch):
    module = import_runtime_semantics(monkeypatch)

    preserved_component = FakeComponent(
        "RoadMesh",
        "StaticMeshComponent",
        mesh=FakeMesh("SM_road", "/Game/Road/SM_road"),
        stencil=2,
        render_custom_depth=False,
    )
    actor = FakeActor("RoadDecal01", "StaticMeshActor", [preserved_component])

    controller = module.RuntimeSemanticStencilController(
        actor_provider=lambda: [actor],
        log_fn=lambda msg: None,
        warn_fn=lambda msg: None,
    )

    stats = controller.apply(
        {
            "runtime": {
                "auto_semantic_stencil": {
                    "enabled": True,
                    "preserve_existing": True,
                }
            }
        }
    )

    assert stats["changed"] == 0
    assert stats["preserved"] == 1
    assert stats["preserved_enabled"] == 1
    assert stats["preserved_by_stencil"] == {"2": 1}
    assert preserved_component.props["render_custom_depth"] is True
    assert preserved_component.props["custom_depth_stencil_value"] == 2


def test_runtime_semantic_stencil_collects_unmatched_samples_before_unknown(monkeypatch):
    module = import_runtime_semantics(monkeypatch)

    first = FakeComponent("MysteryComponentA", "StaticMeshComponent")
    second = FakeComponent("MysteryComponentB", "StaticMeshComponent")
    actor = FakeActor("MysteryActor", "RuntimeActor", [first, second])

    controller = module.RuntimeSemanticStencilController(
        actor_provider=lambda: [actor],
        log_fn=lambda msg: None,
        warn_fn=lambda msg: None,
    )

    stats = controller.apply(
        {
            "runtime": {
                "auto_semantic_stencil": {
                    "enabled": True,
                    "unknown_for_unmatched": True,
                    "unmatched_sample_limit": 1,
                }
            }
        }
    )

    assert stats["changed"] == 2
    assert stats["changed_by_class"] == {"unknown": 2}
    assert len(stats["unmatched_samples"]) == 1
    assert "MysteryActor" in stats["unmatched_samples"][0]
    assert "MysteryComponentA" in stats["unmatched_samples"][0]


def test_runtime_semantic_stencil_ignores_capture_rig_when_unknown_is_enabled(monkeypatch):
    module = import_runtime_semantics(monkeypatch)

    capture_component = FakeComponent("SceneCaptureComponent2D", "SceneCaptureComponent2D")
    actor = FakeActor("SC_RGB", "SceneCapture2D", [capture_component])

    controller = module.RuntimeSemanticStencilController(
        actor_provider=lambda: [actor],
        log_fn=lambda msg: None,
        warn_fn=lambda msg: None,
    )

    stats = controller.apply(
        {
            "runtime": {
                "auto_semantic_stencil": {
                    "enabled": True,
                    "unknown_for_unmatched": True,
                }
            }
        }
    )

    assert stats["changed"] == 0
    assert stats["ignored"] == 1
    assert capture_component.props["custom_depth_stencil_value"] == 0


def test_runtime_semantic_stencil_prioritizes_components_near_capture_point(monkeypatch):
    module = import_runtime_semantics(monkeypatch)

    far_component = FakeComponent(
        "SM_Tower_Facade",
        "StaticMeshComponent",
        mesh=FakeMesh("SM_building_wall", "/Game/Buildings/SM_building_wall"),
        location=FakeVector(10000, 0, 0),
    )
    near_component = FakeComponent(
        "SM_Road_Asphalt",
        "StaticMeshComponent",
        mesh=FakeMesh("SM_road_asphalt", "/Game/Roads/SM_road_asphalt"),
        location=FakeVector(100, 0, 0),
    )
    far_actor = FakeActor("FarBuilding", "StaticMeshActor", [far_component])
    near_actor = FakeActor("NearStreet", "StaticMeshActor", [near_component])

    controller = module.RuntimeSemanticStencilController(
        actor_provider=lambda: [far_actor, near_actor],
        log_fn=lambda msg: None,
        warn_fn=lambda msg: None,
    )

    stats = controller.apply(
        {
            "runtime": {
                "auto_semantic_stencil": {
                    "enabled": True,
                    "component_order": "capture_distance",
                    "capture_point": {"x": 0, "y": 0, "z": 0},
                    "max_components": 1,
                    "unknown_for_unmatched": False,
                }
            }
        }
    )

    assert stats["changed_by_class"] == {"road": 1}
    assert stats["component_order"] == "capture_distance"
    assert near_component.props["custom_depth_stencil_value"] == 2
    assert far_component.props["custom_depth_stencil_value"] == 0
    assert stats["stopped_at_limit"] is True
