import importlib
import sys
import types


class FakeComponent:
    pass


class FakeSceneCatalog:
    def build_component_index(self):
        return {}

    def resolve_component_descriptor(self, component_index, rule):
        return {"component_ref": FakeComponent()}, "ok"


class FakeAnnotator:
    def detect_ignore_stencil(self, class_table_csv):
        return 254

    def supports_stencil(self, component):
        return True

    def inspect_translucent_material_risk(self, component):
        return ""


def import_validation_module(monkeypatch):
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(
        sys.modules,
        "unreal",
        types.SimpleNamespace(ScopedEditorTransaction=lambda name: object()),
    )
    for module_name in (
        "common",
        "argus_backends.ue",
        "argus_backends.ue.editor",
        "argus_components",
        "argus_components.annotation_control",
        "argus_components.scene_objects",
        "validate_semantic_map",
    ):
        sys.modules.pop(module_name, None)
    return importlib.import_module("validate_semantic_map")


def test_validation_reports_deferred_backend_strategy(monkeypatch):
    module = import_validation_module(monkeypatch)
    rule = {
        "target_type": "material_slot",
        "actor_name": "Building_01",
        "component_name": "Mesh",
        "material_slot": "Windows",
        "semantic_class": "glass",
        "render_main_pass": "true",
        "render_custom_depth": "true",
        "stencil": "12",
    }
    written = {}

    monkeypatch.setattr(
        module,
        "load_json_config",
        lambda config_path=None: (
            {
                "semantics": {
                    "semantic_map_csv": "semantic_map.csv",
                    "class_table_csv": "semantic_classes.csv",
                    "unknown_stencil": 250,
                },
                "output": {"semantic_validation_csv": "validation.csv"},
            },
            "pipeline_config.json",
        ),
    )
    monkeypatch.setattr(module, "resolve_path", lambda value: value)
    monkeypatch.setattr(module, "semantic_map_to_stencil", lambda path: [rule])
    monkeypatch.setattr(module, "SceneObjectCatalog", FakeSceneCatalog)
    monkeypatch.setattr(module, "AnnotationController", FakeAnnotator)
    monkeypatch.setattr(module, "_write_csv", lambda path, rows: written.update(rows=rows))
    monkeypatch.setattr(module, "log", lambda message: None)

    result = module.validate_semantic_map()

    row = written["rows"][0]
    assert result["warnings"] == 1
    assert row["status"] == "requires_material_split"
    assert row["backend"] == "ue"
    assert row["strategy"] == "requires_material_split"
    assert row["strategy_executable"] is False
    assert "material slot" in row["strategy_reason"].lower()
