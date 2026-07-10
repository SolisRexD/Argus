import csv
import importlib
import sys
import types

from argus_core.model import AnnotationRule, RenderPolicy, TargetType
from argus_core.planning import StrategyKind


def import_annotation_control(monkeypatch):
    fake_unreal = types.SimpleNamespace(
        ScopedEditorTransaction=lambda name: object(),
    )
    monkeypatch.syspath_prepend("scripts")
    monkeypatch.setitem(sys.modules, "unreal", fake_unreal)
    for module_name in (
        "common",
        "argus_backends.ue",
        "argus_backends.ue.editor",
        "argus_components.annotation_control",
    ):
        sys.modules.pop(module_name, None)
    return importlib.import_module("argus_components.annotation_control")


def test_semantic_rule_builder_adapts_core_rule_and_backend_strategy(monkeypatch):
    module = import_annotation_control(monkeypatch)
    builder = module.SemanticRuleBuilder({"unknown_stencil": 250}, ignore_stencil=254)

    context = builder.build_context(
        {
            "target_type": "material_slot",
            "actor_name": "Building_01",
            "component_name": "Mesh",
            "material_slot": "Windows",
            "semantic_class": "glass",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "12",
        }
    )

    assert isinstance(context.annotation_rule, AnnotationRule)
    assert context.annotation_rule.target.target_type == TargetType.MATERIAL_SLOT
    assert context.annotation_rule.render_policy == RenderPolicy.VISIBLE_LABELED
    assert context.strategy_decision.kind == StrategyKind.REQUIRES_MATERIAL_SPLIT
    assert context.strategy_decision.executable is False
    assert context.render_main_pass is True
    assert context.render_custom_depth is True
    assert context.stencil == 12
    assert context.match_rule()["material_slot"] == "Windows"


class FakeComponent:
    def __init__(self):
        self.properties = {
            "render_in_main_pass": True,
            "render_custom_depth": False,
            "custom_depth_stencil_value": 0,
        }
        self.set_calls = []

    def get_editor_property(self, name):
        return self.properties[name]

    def set_editor_property(self, name, value):
        self.set_calls.append((name, value))
        self.properties[name] = value

    def get_materials(self):
        return []


class FakeSceneCatalog:
    def __init__(self, component):
        self.component = component
        self.resolved_rules = []

    def resolve_component_descriptor(self, component_index, rule):
        self.resolved_rules.append(rule)
        return {"component_ref": self.component}, "ok"


def test_writeback_defers_unsupported_material_slot_without_component_mutation(
    monkeypatch,
    tmp_path,
):
    module = import_annotation_control(monkeypatch)
    monkeypatch.setattr(module, "log", lambda message: None)
    monkeypatch.setattr(module, "warn", lambda message: None)

    class_table = tmp_path / "semantic_classes.csv"
    with class_table.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["semantic_class", "stencil"])
        writer.writeheader()
        writer.writerow({"semantic_class": "ignore", "stencil": "254"})

    component = FakeComponent()
    scene_catalog = FakeSceneCatalog(component)
    logs = module.AnnotationController().apply_writeback(
        rules=[
            {
                "target_type": "material_slot",
                "actor_name": "Building_01",
                "component_name": "Mesh",
                "material_slot": "Windows",
                "semantic_class": "glass",
                "render_main_pass": "true",
                "render_custom_depth": "true",
                "stencil": "12",
            }
        ],
        sem_cfg={"unknown_stencil": 250},
        class_table_csv=str(class_table),
        scene_catalog=scene_catalog,
        component_index={},
        dry_run=False,
    )

    assert component.set_calls == []
    assert logs[0]["status"] == "requires_material_split"
    assert logs[0]["strategy"] == "requires_material_split"
    assert logs[0]["strategy_executable"] is False
    assert scene_catalog.resolved_rules[0]["material_slot"] == "Windows"
