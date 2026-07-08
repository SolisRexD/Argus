import csv
from pathlib import Path

from argus_core.semantics import infer_semantic_stencil
from argus_core.semantics.auto_stencil import RULES


def test_infers_citysample_vehicle_from_actor_and_mesh_names():
    decision = infer_semantic_stencil(
        [
            "BP_vehCar_vehicle07_Sandbox_C",
            "VehicleMesh",
            "SM_car_body",
        ]
    )

    assert decision.semantic_class == "vehicle"
    assert decision.stencil == 6


def test_infers_building_before_generic_prop():
    decision = infer_semantic_stencil(
        [
            "CityBlock_Building_A",
            "SM_Facade_Window_Wall",
            "MI_GlassWindow",
        ]
    )

    assert decision.semantic_class == "building"
    assert decision.stencil == 5


def test_ignores_argus_scene_capture_helpers():
    decision = infer_semantic_stencil(["SC_RGB", "SceneCaptureComponent2D"])

    assert decision is None


def test_can_route_unmatched_components_to_unknown_stencil():
    decision = infer_semantic_stencil(["MysteryRuntimeActor"], unknown_for_unmatched=True)

    assert decision.semantic_class == "unknown"
    assert decision.stencil == 250


def test_infers_citysample_pedestrian_from_mass_crowd_names():
    decision = infer_semantic_stencil(
        [
            "MassCrowdRepresentationActor",
            "CrowdCharacterMesh",
            "SKM_pedestrian_female_01",
        ]
    )

    assert decision.semantic_class == "pedestrian"
    assert decision.stencil == 22


def test_infers_citysample_sidewalk_and_curb_aliases_before_generic_building():
    sidewalk = infer_semantic_stencil(
        [
            "SM_CSW_Sidewalk_Concrete",
            "/Game/CitySample/Environment/Sidewalks/MI_Concrete_Paving",
        ]
    )
    curb = infer_semantic_stencil(
        [
            "SM_CurbStone_Border",
            "/Game/CitySample/Environment/Road/MI_KerbStone",
        ]
    )

    assert sidewalk.semantic_class == "sidewalk"
    assert sidewalk.stencil == 3
    assert curb.semantic_class == "curb_border"
    assert curb.stencil == 4


def test_infers_citysample_building_hlod_and_window_aliases():
    decision = infer_semantic_stencil(
        [
            "HLOD_CityBlock_Lot_17",
            "SM_Tower_Exterior_Glass",
            "MI_ConcreteFacade",
        ]
    )

    assert decision.semantic_class == "building"
    assert decision.stencil == 5


def test_infers_citysample_street_furniture_and_foliage_aliases():
    traffic = infer_semantic_stencil(["SM_TrafficLight_Intersection_Signal"])
    street_light = infer_semantic_stencil(["SM_StreetLight_LampPost"])
    tree = infer_semantic_stencil(["SM_Oak_Foliage_Canopy"])
    bush = infer_semantic_stencil(["SM_Hedge_Cluster"])

    assert traffic.semantic_class == "traffic_sign"
    assert traffic.stencil == 8
    assert street_light.semantic_class == "prop"
    assert street_light.stencil == 20
    assert tree.semantic_class == "tree"
    assert tree.stencil == 15
    assert bush.semantic_class == "bush"
    assert bush.stencil == 16


def test_runtime_rule_classes_exist_in_semantic_palette():
    root = Path(__file__).resolve().parents[1]
    palette_path = root / "config" / "semantic_classes.csv"
    with palette_path.open("r", encoding="utf-8-sig", newline="") as f:
        palette_classes = {row["semantic_class"] for row in csv.DictReader(f)}

    rule_classes = {semantic_class for semantic_class, _, _ in RULES}

    assert rule_classes <= palette_classes
