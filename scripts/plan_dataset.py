"""Offline: compile a new immutable dataset plan, without connecting to UE."""
import argparse
import copy
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from argus_core.dataset import (COMPILER_VERSION, atomic_json, class_mapping, sample_poses,
                                scene_recipe, scene_split, sha256_file, source_manifest, validate_spec)
from argus_core.pcg import fingerprint, validate_recipe


def create(spec_path, output):
    spec = validate_spec(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    directory = Path(output).resolve()
    if directory.exists():
        raise FileExistsError("Plan directory exists; resume it or choose a new output directory")
    source = source_manifest(ROOT)
    base = json.loads((ROOT / "config/pipeline_pcg.json").read_text(encoding="utf-8"))
    template = json.loads((ROOT / "config/pcg_lab_recipe.json").read_text(encoding="utf-8"))
    table = ROOT / base["semantics"]["class_table_csv"]
    with table.open(encoding="utf-8-sig", newline="") as stream:
        classes = class_mapping(csv.DictReader(stream))
    plan = {"schema_version": 1, "compiler_version": COMPILER_VERSION, "spec": spec,
            "source_files": source, "source_sha256": fingerprint(source),
            "class_table_sha256": sha256_file(table), "classes": classes, "scenes": []}
    identity = fingerprint({"spec": spec, "source": source, "base": base,
                            "template": template, "classes": plan["class_table_sha256"]})[:16]
    width, height = spec["resolution"]
    for preset in spec["environments"]:
        for layout_seed in spec["layout_seeds"]:
            group = f"{preset}_layout{layout_seed}"
            # Minus signs are valid seed values but invalid in UE package names.
            asset_id = ("D_" + identity + "/" + group).replace("-", "neg")
            recipe = scene_recipe(template, preset, layout_seed, asset_id)
            validate_recipe(recipe, classes)
            for pcg_seed in spec["pcg_seeds"]:
                key = group + "_pcg" + str(pcg_seed)
                cfg = copy.deepcopy(base)
                cfg["assets"]["root"] = "/Game/Argus/PCG/Datasets/D_" + identity + "/Capture"
                cfg["semantics"].update(mask_encoding="stencil_gray", class_table_csv=str(directory / "semantic_classes.csv"))
                cfg["render_target"].update(width=width, height=height)
                cfg["capture"]["camera_intrinsics"].update(image_width=width, image_height=height)
                cfg["runtime"].update(enabled=True, warmup_seconds=spec["warmup_seconds"],
                                      pause_after_warmup=False, resume_after_capture=False,
                                      wait_for_streaming=False, move_player_to_capture=False)
                cfg["pcg"]["recipe"] = str(directory / "recipes" / (group + ".json"))
                plan["scenes"].append({"key": key, "group": group, "pcg_seed": pcg_seed,
                    "split": scene_split(group, spec["split_seed"], spec["split_ratios"]),
                    "recipe": recipe, "config": cfg,
                    "poses": sample_poses(group, spec["camera_seed"], spec["views_per_scene"])})
    plan["plan_sha256"] = fingerprint(plan)
    directory.mkdir(parents=True)
    (directory / "recipes").mkdir()
    (directory / "configs").mkdir()
    (directory / "semantic_classes.csv").write_bytes(table.read_bytes())
    for scene in plan["scenes"]:
        atomic_json(directory / "recipes" / (scene["group"] + ".json"), scene["recipe"])
        atomic_json(directory / "configs" / (scene["key"] + ".json"), scene["config"])
    atomic_json(directory / "plan.json", plan)
    print(f"Planned {len(plan['scenes'])} scenes / {len(plan['scenes']) * spec['views_per_scene']} RGB+ID pairs: {directory}")
    return directory / "plan.json"


def load_plan(path, check_source=True):
    path = Path(path).resolve()
    plan = json.loads(path.read_text(encoding="utf-8"))
    if fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise ValueError("Dataset plan was modified")
    if check_source and fingerprint(source_manifest(ROOT)) != plan["source_sha256"]:
        raise ValueError("Capture source changed; create a new dataset plan")
    if sha256_file(path.parent / "semantic_classes.csv") != plan["class_table_sha256"]:
        raise ValueError("Dataset class table changed")
    for scene in plan["scenes"]:
        for actual, expected in ((path.parent / "recipes" / (scene["group"] + ".json"), scene["recipe"]),
                                 (path.parent / "configs" / (scene["key"] + ".json"), scene["config"])):
            if json.loads(actual.read_text(encoding="utf-8")) != expected:
                raise ValueError("Dataset recipe/config changed: " + str(actual))
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(ROOT / "config/dataset_pcg.json"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    create(args.spec, args.output)
