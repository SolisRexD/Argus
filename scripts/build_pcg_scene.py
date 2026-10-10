"""Create the independent PCG lab once, before PIE. Never overwrite assets."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import unreal
from argus_backends.ue.pcg import TAG, build_graph, load_recipe
from argus_components import DualCaptureSetupService, SemanticPostProcessBuilder
from common import load_json_config, make_rotator
from create_capture_test_level import CaptureLabBuilder

DEFAULT_CONFIG = str(ROOT / "config/pipeline_pcg.json")


class PCGLabBaseBuilder(CaptureLabBuilder):
    def __init__(self, classes):
        super().__init__()
        self.classes = classes

    def mesh(self, key, *args, **kwargs):
        if key in ("tree", "car", "bench", "trash"):
            return None  # These objects are generated exclusively by the PCG graph.
        actor = super().mesh(key, *args, **kwargs)
        semantic = kwargs.get("semantic", "prop")
        actor.tags = ["ArgusBase:" + semantic]
        actor.static_mesh_component.set_render_custom_depth(True)
        actor.static_mesh_component.set_custom_depth_stencil_value(self.classes[semantic])
        return actor


def create(config_path=DEFAULT_CONFIG, builder=None):
    cfg, _ = load_json_config(config_path)
    recipe, classes = load_recipe(cfg["pcg"]["recipe"], cfg["semantics"]["class_table_csv"])
    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    if editor.get_game_world():
        raise RuntimeError("Stop PIE before building the PCG lab")
    if unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():
        raise RuntimeError("Save the currently modified map first")
    for path in (recipe["map_path"], recipe["graph_path"]):
        if unreal.EditorAssetLibrary.does_asset_exist(path):
            raise RuntimeError("Asset exists; open it or choose new recipe paths: " + path)
    if cfg["runtime"]["profile"] != "generic" or cfg["runtime"]["auto_semantic_stencil"]["enabled"]:
        raise ValueError("PCG lab requires generic profile and explicit semantics")
    builder = builder or PCGLabBaseBuilder(classes)
    for branch in recipe["branches"]:
        if not isinstance(unreal.load_asset(branch["mesh"]), unreal.StaticMesh):
            raise RuntimeError("Missing source art: " + branch["mesh"])
    graph = build_graph(recipe, classes)
    unreal.EditorAssetLibrary.make_directory(recipe["map_path"].rsplit("/", 1)[0])
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.new_level(recipe["map_path"], False):
        raise RuntimeError("Unable to create PCG map")
    editor.get_editor_world().get_world_settings().set_editor_property("default_game_mode", unreal.GameModeBase)
    builder.build()
    actor = builder.spawn(unreal.PCGVolume, recipe["actor_label"], folder="PCG")
    actor.tags = [TAG]
    component = actor.get_component_by_class(unreal.PCGComponent)
    component.set_editor_property("generation_trigger", unreal.PCGComponentGenerationTrigger.GENERATE_ON_DEMAND)
    component.set_editor_property("is_component_partitioned", False)
    component.set_editor_property("generate_on_drop_when_trigger_on_demand", False)
    component.set_editor_property("regenerate_in_editor", False)
    component.set_editor_property("seed", recipe["seed"])
    component.set_graph(graph)
    SemanticPostProcessBuilder().build_material_and_targets(cfg)
    DualCaptureSetupService().setup(cfg)
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(unreal.Vector(6500, -7500, 6500), make_rotator(-32, 139, 0))
    if not level.save_current_level():
        raise RuntimeError("PCG map save failed")
    print("ARGUS_PCG_LAB_CREATED", recipe["map_path"])
    return recipe["map_path"]


if __name__ == "__main__":
    create()
