"""Create a small, ordinary UE 5.8 map using existing static CitySample art.

The map uses native GameModeBase, no World Partition or CitySample gameplay.
Creation fails if the destination already exists; it never clears another map.
Run before PIE. Assets/poses used by capture come from the pipeline config.
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import unreal

from argus_components import DualCaptureSetupService, SemanticPostProcessBuilder
from common import load_json_config, make_rotator

MAP_PATH = "/Game/Argus/Maps/Argus_CaptureLab"
ART_ROOT = "/Game/Argus/CaptureLab/Materials"
MESHES = {
    "cube": "/Engine/BasicShapes/Cube",
    "road": "/Game/Road/Kit_City_Road/SM_ROAD_19_20_0_0_road",
    "facade": "/Game/Building/CH/D/Kit_Bldg_CHD_L4_A/Mesh/SM_BLDG_CHD_L4_A_Wall_03_N1",
    "car": "/Game/Vehicle/vehCar_vehicle06/Mesh/SM_vehCar_vehicle06",
    "tree": "/Game/Prop/Kit_Tree_Birch/Mesh/SM_Tree_Birch_a",
    "bench": "/Game/Prop/Kit_bench_RR/mesh/SM_park_bench_N01",
    "trash": "/Game/Prop/Kit_Trashcan_A/Mesh/SM_Trashcan_A_01",
}


class CaptureLabBuilder:
    def __init__(self):
        self.actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        self.meshes = {key: unreal.load_asset(path) for key, path in MESHES.items()}
        missing = [MESHES[key] for key, asset in self.meshes.items() if not asset]
        if missing:
            raise RuntimeError("Missing source art: " + ", ".join(missing))
        self.materials = {}
        self.manifest = []

    def material(self, name, color):
        path = ART_ROOT + "/M_" + name
        material = unreal.load_asset(path)
        if not material:
            material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                "M_" + name, ART_ROOT, unreal.Material, unreal.MaterialFactoryNew())
            base = unreal.MaterialEditingLibrary.create_material_expression(
                material, unreal.MaterialExpressionConstant3Vector, -300, 0)
            base.set_editor_property("constant", unreal.LinearColor(*color, 1.0))
            unreal.MaterialEditingLibrary.connect_material_property(
                base, "", unreal.MaterialProperty.MP_BASE_COLOR)
            rough = unreal.MaterialEditingLibrary.create_material_expression(
                material, unreal.MaterialExpressionConstant, -300, 180)
            rough.set_editor_property("r", 0.8)
            unreal.MaterialEditingLibrary.connect_material_property(
                rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
            unreal.MaterialEditingLibrary.recompile_material(material)
            unreal.EditorAssetLibrary.save_loaded_asset(material)
        self.materials[name] = material

    def spawn(self, cls, name, location=(0, 0, 0), rotation=(0, 0, 0), folder="Environment"):
        actor = self.actors.spawn_actor_from_class(
            cls, unreal.Vector(*location), make_rotator(*rotation))
        if not actor:
            raise RuntimeError("Actor spawn failed: " + name)
        actor.set_actor_label(name)
        actor.set_folder_path("Argus/" + folder)
        return actor

    def mesh(self, key, label, center, size=None, yaw=0, scale=1, material=None,
             semantic="prop", folder="Props"):
        """Place bounding-box center at XY and the mesh bottom at center.z."""
        asset = self.meshes[key]
        box = asset.get_bounding_box()
        low = (box.min.x, box.min.y, box.min.z)
        high = (box.max.x, box.max.y, box.max.z)
        factors = [size[i] / max(high[i] - low[i], 0.01) for i in range(3)] if size else [scale] * 3
        local = [(low[i] + high[i]) * 0.5 * factors[i] for i in range(2)]
        angle = math.radians(yaw)
        dx = local[0] * math.cos(angle) - local[1] * math.sin(angle)
        dy = local[0] * math.sin(angle) + local[1] * math.cos(angle)
        location = (center[0] - dx, center[1] - dy, center[2] - low[2] * factors[2])
        actor = self.spawn(unreal.StaticMeshActor, label, location, (0, yaw, 0), folder)
        component = actor.static_mesh_component
        component.set_static_mesh(asset)
        component.set_mobility(unreal.ComponentMobility.STATIC)
        actor.set_actor_scale3d(unreal.Vector(*factors))
        if material:
            component.set_material(0, self.materials[material])
        # Deliberately leave stencil=0: validation must exercise Argus inference.
        self.manifest.append({"label": label, "mesh": MESHES[key], "semantic_class": semantic,
                              "actor_path": actor.get_path_name()})
        return actor

    def building(self, label, x, y, floors):
        width = 1800
        height = floors * 500
        self.mesh("cube", label + "_BuildingCore", (x, y, 20),
                  (width - 100, width - 100, height), material="Building",
                  semantic="building", folder="Buildings")
        # The facade kit faces local +X. Repeat it around the four sides.
        for level in range(floors):
            for side in range(4):
                angle = math.radians(side * 90)
                for bay in range(3):
                    offset = (bay - 1) * 600
                    px = x + 865 * math.cos(angle) - offset * math.sin(angle)
                    py = y + 865 * math.sin(angle) + offset * math.cos(angle)
                    self.mesh("facade", f"{label}_Facade_{level}_{side}_{bay}",
                              (px, py, 20 + level * 500), (140, 600, 500), yaw=side * 90,
                              semantic="building", folder="Buildings")
        self.mesh("cube", label + "_Roof", (x, y, height + 20), (1840, 1840, 40),
                  material="Roof", semantic="building", folder="Buildings")

    def build(self):
        for name, color in {
            "Terrain": (0.10, 0.14, 0.09), "Sidewalk": (0.36, 0.33, 0.28),
            "Building": (0.30, 0.27, 0.23), "Roof": (0.09, 0.10, 0.11),
            "RoadMarking": (0.78, 0.75, 0.54), "Grass": (0.07, 0.20, 0.04),
            "Curb": (0.48, 0.47, 0.43),
        }.items():
            self.material(name, color)
        self.mesh("cube", "Terrain_Base", (0, 0, -90), (9500, 9500, 80),
                  material="Terrain", semantic="terrain", folder="Ground")
        for y in (-3000, -1000, 1000, 3000):
            self.mesh("road", "Road_Segment_" + str(y), (0, y, -2), (2000, 2000, 58),
                      semantic="road", folder="Ground")
        for x in (-2600, 2600):
            self.mesh("cube", "Sidewalk_" + str(x), (x, 0, 0), (3150, 8000, 20),
                      material="Sidewalk", semantic="sidewalk", folder="Ground")
            self.mesh("cube", "Curb_" + str(x), (-1020 if x < 0 else 1020, 0, 0),
                      (45, 8000, 30), material="Curb", semantic="curb_border", folder="Ground")
        for y in range(-3600, 3800, 500):
            self.mesh("cube", "RoadMarking_" + str(y), (0, y, 57), (12, 240, 1),
                      material="RoadMarking", semantic="road", folder="Ground")
        self.building("West_A", -2850, -2100, 3)
        self.building("West_B", -2850, 2100, 4)
        self.building("East_A", 2850, 2250, 3)
        for i, (x, y, yaw) in enumerate(((-520, -1900, 90), (520, 500, -90), (520, 2650, -90))):
            self.mesh("car", f"Vehicle_{i}", (x, y, 58), yaw=yaw, semantic="vehicle")
        for i, (x, y) in enumerate(((1600, -2850), (3500, -2800), (3400, -700),
                                  (1550, -550), (-1350, -3500), (-1350, 400), (-1350, 3300))):
            self.mesh("cube", f"Grass_Patch_{i}", (x, y, 20), (500, 500, 12),
                      material="Grass", semantic="grass", folder="Landscape")
            self.mesh("tree", f"Tree_{i}", (x, y, 32), scale=0.8,
                      semantic="tree", folder="Landscape")
        for i, (x, y, yaw) in enumerate(((2400, -2700, 0), (3300, -1700, 90),
                                       (2100, -650, 180), (-1350, -400, 90))):
            self.mesh("bench", f"Bench_{i}", (x, y, 20), yaw=yaw, semantic="bench")
            self.mesh("trash", f"Trash_{i}", (x + 180, y + 100, 20), semantic="prop")
        self.lighting()

    def lighting(self, sun_pitch=-48, sun_yaw=-35, intensity=40000.0, exposure=12.0):
        self.spawn(unreal.PlayerStart, "PlayerStart", (6500, -7500, 6500), (-32, 139, 0))
        sun = self.spawn(unreal.DirectionalLight, "Sun", (0, 0, 5000), (sun_pitch, sun_yaw, 0))
        light = sun.get_component_by_class(unreal.DirectionalLightComponent)
        light.set_mobility(unreal.ComponentMobility.MOVABLE)
        light.set_intensity(intensity)
        light.set_atmosphere_sun_light(True)
        self.spawn(unreal.SkyAtmosphere, "SkyAtmosphere")
        sky = self.spawn(unreal.SkyLight, "SkyLight")
        skylight = sky.get_component_by_class(unreal.SkyLightComponent)
        skylight.set_mobility(unreal.ComponentMobility.MOVABLE)
        skylight.set_editor_property("real_time_capture", True)
        skylight.set_intensity(1.0)
        volume = self.spawn(unreal.PostProcessVolume, "FixedExposure")
        volume.set_editor_property("unbound", True)
        settings = volume.get_editor_property("settings")
        for name, value in {"auto_exposure_min_brightness": exposure,
                            "auto_exposure_max_brightness": exposure, "auto_exposure_bias": 0.0,
                            "motion_blur_amount": 0.0, "bloom_intensity": 0.0,
                            "vignette_intensity": 0.0}.items():
            settings.set_editor_property("override_" + name, True)
            settings.set_editor_property(name, value)
        volume.set_editor_property("settings", settings)


def create(config_path=None, map_path=MAP_PATH):
    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    if editor.get_game_world():
        raise RuntimeError("Stop PIE before creating the capture test level")
    if unreal.EditorAssetLibrary.does_asset_exist(map_path):
        raise RuntimeError("Map already exists; open it or choose a new map_path: " + map_path)
    if unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():
        raise RuntimeError("Save the currently modified map before creating a new level")
    cfg, _ = load_json_config(config_path)
    if cfg.get("runtime", {}).get("profile") != "generic":
        raise ValueError("The clean level requires the generic pipeline profile")
    builder = CaptureLabBuilder()  # Validate source art before switching worlds.
    unreal.EditorAssetLibrary.make_directory(map_path.rsplit("/", 1)[0])
    level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level.new_level(map_path, False):
        raise RuntimeError("Unable to create blank level: " + map_path)
    world = editor.get_editor_world()
    world.get_world_settings().set_editor_property("default_game_mode", unreal.GameModeBase)
    builder.build()
    SemanticPostProcessBuilder().build_material_and_targets(cfg)
    DualCaptureSetupService().setup(cfg)
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(
        unreal.Vector(6500, -7500, 6500), make_rotator(-32, 139, 0))
    if not level.save_current_level():
        raise RuntimeError("Unable to save capture test level")
    manifest_path = ROOT / "output/capture_lab/scene_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"map": map_path, "world_partition": False,
        "game_mode": "/Script/Engine.GameModeBase", "objects": builder.manifest},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print("ARGUS_CAPTURE_LAB_CREATED", map_path, len(builder.manifest))
    return map_path


if __name__ == "__main__":
    create()
