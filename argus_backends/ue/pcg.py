"""UE 5.8 CPU/non-partitioned PCG authoring and realized-output inspection."""
import csv
import hashlib
import json
import re
from pathlib import Path

import unreal

from argus_core.pcg import canonical_instances, fingerprint, validate_instances, validate_recipe

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = ROOT / "config/pcg_lab_recipe.json"
TAG = "ArgusPCGLabV1"


def load_recipe(path=None, class_table=None):
    path = Path(path or DEFAULT_RECIPE)
    if not path.is_absolute():
        path = ROOT / path
    table = Path(class_table or ROOT / "config/semantic_classes.csv")
    if not table.is_absolute():
        table = ROOT / table
    with table.open(encoding="utf-8-sig", newline="") as handle:
        classes = {r["semantic_class"]: int(r["stencil"]) for r in csv.DictReader(handle)}
    recipe = json.loads(path.read_text(encoding="utf-8"))
    validate_recipe(recipe, classes)
    return recipe, classes


def current_world():
    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    return editor.get_game_world() or editor.get_editor_world()


def get_generator(recipe):
    world = current_world()
    path = re.sub(r"UEDPIE_\d+_", "", world.get_path_name()).split(".")[0]
    if path != recipe["map_path"]:
        raise RuntimeError("Open the recipe map first: " + recipe["map_path"])
    actors = [a for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.PCGVolume)
              if a.get_actor_label() == recipe["actor_label"] and TAG in map(str, a.tags)]
    if len(actors) != 1:
        raise RuntimeError("Expected one owned PCG generator, got " + str(len(actors)))
    actor = actors[0]
    component = actor.get_component_by_class(unreal.PCGComponent)
    if component.is_component_partitioned or component.generation_trigger != unreal.PCGComponentGenerationTrigger.GENERATE_ON_DEMAND:
        raise RuntimeError("Only non-partitioned OnDemand PCG is supported")
    graph = component.get_graph()
    if not graph or graph.get_path_name().split(".")[0] != recipe["graph_path"]:
        raise RuntimeError("PCG graph does not match recipe")
    if graph.get_editor_property("use_hierarchical_generation") or graph.get_editor_property("use_actor_componentless_generation"):
        raise RuntimeError("Hierarchical/componentless PCG is not supported")
    # EditorAssetLibrary metadata helpers reject calls during PIE. Description is
    # a reflected property on the graph itself and survives the PIE transition.
    if str(graph.get_editor_property("description")) != "ArgusRecipeSHA256:" + fingerprint(recipe):
        raise RuntimeError("Recipe changed since graph build; create new map/graph paths")
    return actor, component


def build_graph(recipe, classes):
    path = recipe["graph_path"]
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        raise RuntimeError("Refusing to overwrite graph: " + path)
    folder, name = path.rsplit("/", 1)
    unreal.EditorAssetLibrary.make_directory(folder)
    # AssetTools.create_asset calls FactoryCreateNew directly, bypassing the UI
    # ConfigureProperties/template picker. bSkipTemplateSelection is not Python-exposed.
    graph = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.PCGGraph, unreal.PCGGraphFactory())
    if not graph:
        raise RuntimeError("PCG graph creation failed")
    for field in ("use_hierarchical_generation", "use_actor_componentless_generation",
                  "auto_refresh_on_quality_settings_changed", "auto_refresh_on_render_state_refresh"):
        graph.set_editor_property(field, False)
    for index, branch in enumerate(recipe["branches"]):
        mesh = unreal.load_asset(branch["mesh"])
        if not isinstance(mesh, unreal.StaticMesh):
            raise RuntimeError("Missing static mesh: " + branch["mesh"])
        grid_node, grid = graph.add_node_of_type(unreal.PCGCreatePointsGridSettings)
        grid.set_editor_property("grid_extents", unreal.Vector(0, branch["count"] * branch["spacing"] / 2, 0))
        grid.set_editor_property("cell_size", unreal.Vector(100, branch["spacing"], 100))
        grid.set_editor_property("coordinate_space", unreal.PCGCoordinateSpace.WORLD)
        grid.set_editor_property("point_position", unreal.PCGPointPosition.CELL_CENTER)
        grid.set_editor_property("cull_points_outside_volume", False)
        transform_node, transform = graph.add_node_of_type(unreal.PCGTransformPointsSettings)
        center = list(branch["center"])
        center[2] -= mesh.get_bounding_box().min.z * branch["scale"]
        for bound, sign in (("min", -1), ("max", 1)):
            transform.set_editor_property("offset_" + bound, unreal.Vector(*[
                center[i] + sign * branch["jitter"][i] for i in range(3)]))
            transform.set_editor_property("scale_" + bound, unreal.Vector(*([branch["scale"]] * 3)))
        transform.set_editor_property("rotation_min", unreal.Rotator(pitch=0, yaw=branch["yaw"][0], roll=0))
        transform.set_editor_property("rotation_max", unreal.Rotator(pitch=0, yaw=branch["yaw"][1], roll=0))
        transform.set_editor_property("seed", 1000 + index)
        transform.set_editor_property("execute_on_gpu", False)
        spawner_node, spawner = graph.add_node_of_type(unreal.PCGStaticMeshSpawnerSettings)
        spawner.set_editor_property("execute_on_gpu", False)
        spawner.set_mesh_selector_type(unreal.PCGMeshSelectorWeighted)
        descriptor = unreal.PCGSoftISMComponentDescriptor()
        for field, value in {
            "static_mesh": mesh, "render_custom_depth": True,
            "custom_depth_stencil_value": classes[branch["semantic_class"]],
            "component_tags": [TAG, "ArgusRule:" + branch["id"], "ArgusClass:" + branch["semantic_class"]],
            "component_class": unreal.InstancedStaticMeshComponent,
            "mobility": unreal.ComponentMobility.STATIC,
            "enable_density_scaling": False, "evaluate_world_position_offset": False,
        }.items():
            descriptor.set_editor_property(field, value)
        entry = unreal.PCGMeshSelectorWeightedEntry()
        entry.set_editor_property("descriptor", descriptor)
        entry.set_editor_property("weight", 1)
        spawner.get_editor_property("mesh_selector_parameters").set_editor_property("mesh_entries", [entry])
        # Keep provenance per branch even when two branches share a descriptor.
        spawner.set_editor_property("allow_merge_different_data_in_same_instanced_components", False)
        graph.add_edge(grid_node, "Out", transform_node, "In")
        graph.add_edge(transform_node, "Out", spawner_node, "In")
        graph.add_edge(spawner_node, "Out", graph.get_editor_property("output_node"), "Out")
        for column, node in enumerate((grid_node, transform_node, spawner_node)):
            node.set_node_position(column * 350, index * 250)
    unreal.EditorAssetLibrary.set_metadata_tag(graph, "ArgusRecipeSHA256", fingerprint(recipe))
    graph.set_editor_property("description", "ArgusRecipeSHA256:" + fingerprint(recipe))
    if not unreal.EditorAssetLibrary.save_loaded_asset(graph):
        raise RuntimeError("Could not save PCG graph")
    return graph


def transform_values(transform):
    p, q, s = transform.translation, transform.rotation, transform.scale3d
    return [round(float(x), 5) for x in (p.x, p.y, p.z, q.x, q.y, q.z, q.w, s.x, s.y, s.z)]


def inspect_instances(actor):
    rows = []
    for component in actor.get_components_by_class(unreal.InstancedStaticMeshComponent):
        tags = list(map(str, component.component_tags))
        rules = [t.split(":", 1)[1] for t in tags if t.startswith("ArgusRule:")]
        labels = [t.split(":", 1)[1] for t in tags if t.startswith("ArgusClass:")]
        if len(rules) != 1 or len(labels) != 1:
            raise RuntimeError("Generated component has ambiguous/missing semantic provenance: " + component.get_path_name())
        for i in range(component.get_instance_count()):
            rows.append({"source_rule": rules[0], "semantic_class": labels[0],
                         "stencil": int(component.custom_depth_stencil_value),
                         "custom_depth": bool(component.render_custom_depth),
                         "mesh": component.static_mesh.get_path_name().split(".")[0],
                         "transform": transform_values(component.get_instance_transform(i, world_space=True)),
                         "component": component.get_path_name(), "instance_index": i})
    return rows


def inspect_base(world, classes):
    rows = []
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor):
        labels = [str(t).split(":", 1)[1] for t in actor.tags if str(t).startswith("ArgusBase:")]
        if len(labels) != 1 or labels[0] not in classes:
            raise RuntimeError("Unlabelled static base actor: " + actor.get_actor_label())
        c = actor.static_mesh_component
        if not c.render_custom_depth or int(c.custom_depth_stencil_value) != classes[labels[0]]:
            raise RuntimeError("Base semantic mismatch: " + actor.get_actor_label())
        rows.append({"id": actor.get_actor_label(), "semantic_class": labels[0],
                     "stencil": int(c.custom_depth_stencil_value),
                     "mesh": c.static_mesh.get_path_name(), "transform": transform_values(actor.get_actor_transform())})
    if not rows:
        raise RuntimeError("Static base is missing")
    return sorted(rows, key=lambda r: r["id"])


def snapshot(recipe, classes):
    actor, component = get_generator(recipe)
    if component.get_editor_property("generation_in_progress"):
        raise RuntimeError("PCG is still generating")
    rows = inspect_instances(actor)
    counts = validate_instances(recipe, classes, rows)
    canonical = canonical_instances(rows)
    # Content IDs are stable within a realized generation, not correspondences
    # between different seeds. Component paths/indices remain debug fields only.
    for row in rows:
        row["instance_id"] = fingerprint({k: row[k] for k in canonical[0]})[:24]
    base = inspect_base(current_world(), classes)
    result = {"map": recipe["map_path"], "graph": recipe["graph_path"],
              "seed": int(component.seed), "recipe_sha256": fingerprint(recipe),
              "class_mapping_sha256": fingerprint(classes), "counts": counts,
              "instances": rows, "static_base": base,
              "world": current_world().get_path_name(),
              "engine_version": unreal.SystemLibrary.get_engine_version()}
    tokens = [str(t).split(":", 1)[1] for t in component.component_tags
              if str(t).startswith("ArgusGeneration:")]
    result["generation_id"] = tokens[0] if len(tokens) == 1 else None
    graph_file = Path(unreal.Paths.project_content_dir()) / (recipe["graph_path"][len("/Game/"):] + ".uasset")
    result["saved_graph_sha256"] = hashlib.sha256(graph_file.read_bytes()).hexdigest()
    result["scene_sha256"] = fingerprint({"instances": canonical, "base": base,
                                          "seed": result["seed"], "recipe": recipe, "classes": classes})
    return result


def assert_capture_ready(cfg):
    pcg = cfg.get("pcg", {})
    proof = cfg.get("_pcg_generation")
    if not proof:
        raise RuntimeError("PCG capture requires a completed generation job; use capture_pcg_scene.run()")
    if cfg.get("runtime", {}).get("auto_semantic_stencil", {}).get("enabled", False):
        raise RuntimeError("PCG explicit semantics require automatic stencil inference disabled")
    if proof.get("class_table_file_sha256"):
        table = Path(cfg["semantics"]["class_table_csv"])
        if not table.is_absolute():
            table = ROOT / table
        if hashlib.sha256(table.read_bytes()).hexdigest() != proof["class_table_file_sha256"]:
            raise RuntimeError("Semantic class table changed during PCG capture")
    recipe, classes = load_recipe(pcg.get("recipe"), cfg["semantics"]["class_table_csv"])
    live = snapshot(recipe, classes)
    if (not proof.get("generation_id") or live["generation_id"] != proof["generation_id"]
            or live["world"] != proof["world"] or live["scene_sha256"] != proof["scene_sha256"]
            or live["saved_graph_sha256"] != proof["saved_graph_sha256"]):
        raise RuntimeError("PCG scene changed after generation; regenerate before capture")
    return live
