"""Offline: validate captured artifacts and export accepted single-channel IDs.

Exit 0 requires every planned frame to pass. This checks encoding, coverage and
provenance, not semantic IoU or photorealism. It preserves rejected captures.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image
from argus_core.dataset import atomic_json, camera_calibration, sha256_file, verify_artifacts
from argus_core.dataset_quality import inspect_pair
from argus_core.pcg import fingerprint
from plan_dataset import load_plan


def validate(plan_path):
    path = Path(plan_path).resolve()
    directory = path.parent
    # Historical datasets remain inspectable after the capture code changes.
    plan = load_plan(path, check_source=False)
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    if state["plan_sha256"] != plan["plan_sha256"]:
        raise ValueError("Checkpoint does not belong to this plan")
    quality = directory / "quality"
    quality.mkdir(exist_ok=True)
    (quality / "labels").mkdir(exist_ok=True)
    rows, scene_errors = [], {}
    size = plan["spec"]["resolution"]
    for scene in plan["scenes"]:
        key = scene["key"]
        try:
            entry = state["scenes"].get(key)
            if not entry or entry["phase"] != "captured":
                raise ValueError("Scene capture has not completed")
            attempt = directory / entry["attempt"]
            if not verify_artifacts(attempt, entry["artifacts"]):
                raise ValueError("Missing/changed artifact; resume capture to create a new attempt")
            result = json.loads((attempt / "result.json").read_text(encoding="utf-8"))
            generation = json.loads((attempt / "generation_manifest.json").read_text(encoding="utf-8"))
            if (result["phase"] != "complete" or result["seed"] != scene["pcg_seed"]
                    or result["success_count"] != len(scene["poses"])
                    or len(result["frames"]) != len(scene["poses"])
                    or generation["recipe_sha256"] != fingerprint(scene["recipe"])
                    or generation["class_table_file_sha256"] != plan["class_table_sha256"]
                    or generation["scene_sha256"] != entry["scene_sha256"]):
                raise ValueError("Scene provenance/count mismatch")
            allowed = {0} | {o["stencil"] for o in generation["static_base"] + generation["instances"]}
            ids = set()
            for i, (frame, pose) in enumerate(zip(result["frames"], scene["poses"])):
                frame_errors = []
                if frame["capture_id"] in ids:
                    frame_errors.append("duplicate_capture_id")
                ids.add(frame["capture_id"])
                for field in ("x", "y", "z", "fov", "pitch", "yaw", "roll"):
                    delta = float(frame[field]) - float(pose[field])
                    if field in ("pitch", "yaw", "roll"):
                        delta = (delta + 180) % 360 - 180
                    if not math.isfinite(delta) or abs(delta) > 0.001:
                        frame_errors.append("camera_" + field + "_mismatch")
                if (frame["pcg_generation_id"] != generation["generation_id"]
                        or frame["pcg_scene_sha256"] != generation["scene_sha256"]
                        or frame["pcg_recipe_sha256"] != generation["recipe_sha256"]
                        or frame["pcg_graph_sha256"] != generation["saved_graph_sha256"]
                        or frame["pcg_seed"] != scene["pcg_seed"]):
                    frame_errors.append("generation_provenance_mismatch")
                paths = {name: Path(frame[name + "_file"]).resolve() for name in ("rgb", "mask")}
                if any(not p.is_relative_to(attempt.resolve()) or p.relative_to(attempt).as_posix() not in entry["artifacts"]
                       for p in paths.values()):
                    raise ValueError("Image is outside the verified attempt")
                try:
                    metrics = inspect_pair(paths["rgb"], paths["mask"], size, allowed)
                except Exception as exc:
                    metrics = {"accepted": False, "errors": ["image_decode: " + str(exc)]}
                metrics["errors"].extend(frame_errors)
                metrics["accepted"] = not metrics["errors"]
                record = {"id": key + "__" + pose["id"], "scene_group": scene["group"],
                    "split": scene["split"], "environment": scene["recipe"]["environment"]["preset"],
                    "layout_seed": scene["recipe"]["environment"]["layout_seed"],
                    "pcg_seed": scene["pcg_seed"], "camera_stratum": pose["stratum"], "pose": pose,
                    "calibration": camera_calibration(pose, *size), "quality": metrics,
                    "plan_sha256": plan["plan_sha256"], "scene_sha256": generation["scene_sha256"],
                    "generation_id": generation["generation_id"], "mask_encoding": "uint8_class_id",
                    "rgb": paths["rgb"].relative_to(directory).as_posix(),
                    "raw_mask": paths["mask"].relative_to(directory).as_posix(),
                    "rgb_sha256": sha256_file(paths["rgb"]), "raw_mask_sha256": sha256_file(paths["mask"])}
                if metrics["accepted"]:
                    label = quality / "labels" / (record["id"] + ".png")
                    with Image.open(paths["mask"]) as image:
                        image.getchannel("R").save(label)
                    record.update(label=label.relative_to(directory).as_posix(), label_sha256=sha256_file(label))
                rows.append(record)
        except Exception as exc:
            scene_errors[key] = str(exc)
    accepted = [row for row in rows if row["quality"]["accepted"] and row["scene_group"] + "_pcg" + str(row["pcg_seed"]) not in scene_errors]
    expected = sum(len(s["poses"]) for s in plan["scenes"])
    report = {"plan_sha256": plan["plan_sha256"], "expected": expected, "inspected": len(rows),
              "accepted": len(accepted), "rejected_or_missing": expected - len(accepted),
              "scene_errors": scene_errors, "passed": len(accepted) == expected and not scene_errors,
              "scope": "encoding, file integrity, pose/provenance and coverage; no pixel ground-truth IoU"}
    for name, values in (("frames.jsonl", rows), ("accepted.jsonl", accepted)):
        temp = quality / (name + ".tmp")
        temp.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in values), encoding="utf-8")
        temp.replace(quality / name)
    atomic_json(quality / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    args = parser.parse_args()
    raise SystemExit(0 if validate(args.plan)["passed"] else 1)
