"""Analytic box-silhouette check for generated yard top-down captures only.

Each warehouse is an axis-aligned opaque cube with known dimensions. Intersect
pixel-centre pinhole rays with those boxes independently of the UE stencil pass.
This does not validate other classes, materials, camera strata or RGB edges.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def box_hits(origin, directions, low, high):
    parallel = np.abs(directions) < 1e-12
    safe = np.where(parallel, 1, directions)
    a, b = (low - origin) / safe, (high - origin) / safe
    near, far = np.minimum(a, b), np.maximum(a, b)
    outside = (origin < low) | (origin > high)
    near = np.where(parallel, np.where(outside, np.inf, -np.inf), near)
    far = np.where(parallel, np.where(outside, -np.inf, np.inf), far)
    return np.max(near, axis=2) <= np.minimum(np.min(far, axis=2), np.inf), np.min(far, axis=2) > 0


def validate(plan_path, output):
    path = Path(plan_path).resolve()
    plan = json.loads(path.read_text(encoding="utf-8"))
    scenes = {s["key"]: s for s in plan["scenes"]}
    rows = [json.loads(line) for line in (path.parent / "quality/accepted.jsonl").read_text(encoding="utf-8").splitlines()]
    results = []
    for row in rows:
        if row["environment"] != "yard" or row["camera_stratum"] != "topdown":
            continue
        scene = scenes[row["scene_group"] + "_pcg" + str(row["pcg_seed"])]
        label = np.asarray(Image.open(path.parent / row["label"]))
        height, width = label.shape
        u, v = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
        k = np.asarray(row["calibration"]["K"])
        c = np.asarray(row["calibration"]["camera_to_world"])
        rays = np.stack(((u - k[0, 2]) / k[0, 0], (v - k[1, 2]) / k[1, 1], np.ones_like(u)), axis=2)
        directions = rays @ c[:3, :3].T
        expected = np.zeros(label.shape, dtype=bool)
        for element in scene["recipe"]["environment"]["elements"]:
            if element.get("semantic") != "building":
                continue
            if element["key"] != "cube" or element.get("yaw", 0) != 0:
                raise ValueError("Fixture requires axis-aligned cubes")
            centre, size = np.asarray(element["center"]), np.asarray(element["size"])
            low = centre - size * [0.5, 0.5, 0]
            high = centre + size * [0.5, 0.5, 1]
            intersect, forward = box_hits(c[:3, 3], directions, low, high)
            expected |= intersect & forward
        actual = label == plan["classes"]["building"]
        intersection = np.count_nonzero(expected & actual)
        union = np.count_nonzero(expected | actual)
        mismatch = actual ^ expected
        # A 1-pixel neighborhood of the analytic silhouette boundary.
        padded = np.pad(expected, 1, constant_values=False)
        neighbors = [padded[dy:dy+height, dx:dx+width] for dy in range(3) for dx in range(3)]
        band = np.logical_or.reduce(neighbors) != np.logical_and.reduce(neighbors)
        results.append({"id": row["id"], "iou": intersection / union if union else None,
                        "expected_pixels": int(expected.sum()), "actual_pixels": int(actual.sum()),
                        "mismatch_pixels": int(mismatch.sum()),
                        "mismatch_outside_one_pixel_boundary": int(np.count_nonzero(mismatch & ~band))})
    report = {"scope": "yard/topdown/building class, analytic pinhole ray versus opaque box silhouette",
              "frames": results, "passed": bool(results) and all(
                  r["iou"] is not None and r["iou"] >= 0.99 and r["mismatch_outside_one_pixel_boundary"] == 0
                  for r in results)}
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raise SystemExit(0 if validate(args.plan, args.output)["passed"] else 1)
