"""Reproducible static environments, camera sampling and dataset bookkeeping.

No Unreal imports. Coordinates are UE centimetres (X forward, Y right, Z up).
"""
import copy
import hashlib
import json
import math
import random
from pathlib import Path

from .pcg import fingerprint, validate_seed

PRESETS = ("urban", "park", "yard")
COMPILER_VERSION = 1


def derived_seed(*parts):
    return int(fingerprint(parts)[:8], 16) & 0x7fffffff


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_manifest(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for folder in ("argus_core", "argus_backends", "scripts")
            for p in sorted((root / folder).rglob("*.py"))}


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def class_mapping(rows):
    classes, used = {}, set()
    for row in rows:
        name, stencil = row["semantic_class"].strip(), int(row["stencil"])
        if not name or name in classes or stencil in used or not 0 <= stencil <= 255:
            raise ValueError("Class names and stencil IDs must be unique, nonempty and in [0, 255]")
        classes[name] = stencil
        used.add(stencil)
    if classes.get("background") != 0:
        raise ValueError("background must have stencil 0")
    return classes


def validate_spec(spec):
    if spec.get("schema_version") != 1:
        raise ValueError("Unsupported dataset specification")
    for key in ("environments", "layout_seeds", "pcg_seeds"):
        values = spec.get(key)
        if not isinstance(values, list) or not values or len(values) != len(set(values)):
            raise ValueError(key + " must be a nonempty unique list")
    if any(e not in PRESETS for e in spec["environments"]):
        raise ValueError("Unknown environment preset")
    for seed in spec["layout_seeds"] + spec["pcg_seeds"] + [spec["camera_seed"], spec["split_seed"]]:
        validate_seed(seed)
    if type(spec["views_per_scene"]) is not int or spec["views_per_scene"] < 3:
        raise ValueError("views_per_scene must be at least 3")
    size = spec["resolution"]
    if len(size) != 2 or any(type(v) is not int or not 64 <= v <= 8192 for v in size):
        raise ValueError("resolution must contain two integers in [64, 8192]")
    ratios = spec["split_ratios"]
    if len(ratios) != 3 or any(not math.isfinite(x) or x < 0 for x in ratios) or not math.isclose(sum(ratios), 1):
        raise ValueError("split_ratios must be train/val/test fractions summing to 1")
    warmup = spec["warmup_seconds"]
    if not math.isfinite(warmup) or warmup < 0:
        raise ValueError("Invalid warmup_seconds")
    return spec


def scene_split(group, seed, ratios):
    # All PCG seeds and camera views of the same base layout stay together.
    u = int(fingerprint([group, seed])[:13], 16) / 16 ** 13
    return "train" if u < ratios[0] else "val" if u < ratios[0] + ratios[1] else "test"


def environment(preset, seed):
    rng = random.Random(derived_seed("layout", preset, seed))
    elements = []

    def mesh(key, label, center, size, material, semantic):
        elements.append(dict(kind="mesh", key=key, label=label, center=center,
                             size=size, material=material, semantic=semantic))

    mesh("cube", "Terrain", [0, 0, -80], [10000, 10000, 80], "Terrain", "terrain")
    mesh("cube", "WestGround", [-2850, 0, 0], [3300, 9000, 20],
         "Grass" if preset == "park" else "Sidewalk", "grass" if preset == "park" else "sidewalk")
    mesh("cube", "EastGround", [2850, 0, 0], [3300, 9000, 20],
         "Grass" if preset == "park" else "Sidewalk", "grass" if preset == "park" else "sidewalk")
    for y in (-3000, -1000, 1000, 3000):
        mesh("road", "Road_" + str(y), [0, y, -2], [2200, 2000, 58], None, "road")
    for side in (-1, 1):
        mesh("cube", "Curb_" + str(side), [side * 1125, 0, 0], [40, 8000, 35], "Curb", "curb_border")
    if preset == "urban":
        for i, (x, y) in enumerate(((-2900, -2300), (-2900, 2300), (2900, -2300), (2900, 2300))):
            elements.append(dict(kind="building", label="Block_" + str(i),
                                 x=x + rng.randint(-200, 200), y=y + rng.randint(-300, 300),
                                 floors=rng.randint(2, 5)))
    elif preset == "park":
        for side in (-1, 1):
            elements.append(dict(kind="building", label="Pavilion_" + str(side),
                                 x=side * 3200, y=3400 + rng.randint(-100, 100), floors=1))
            mesh("cube", "Walkway_" + str(side), [side * 3100, -1300, 20], [500, 4600, 10], "Sidewalk", "sidewalk")
    else:
        for i, (x, y) in enumerate(((-3000, -2200), (-3000, 2400), (3000, 2400))):
            mesh("cube", "Warehouse_" + str(i), [x, y + rng.randint(-200, 200), 20],
                 [rng.randint(1600, 2100), rng.randint(1800, 2400), rng.randint(700, 1400)], "Building", "building")
        for i in range(3):
            mesh("cube", "Crate_" + str(i), [2800 + rng.randint(-200, 200), -2900 + 850 * i, 20],
                 [1000, 500, rng.randint(300, 600)], "Roof", "prop")
    light = random.Random(derived_seed("light", preset, seed))
    return {"preset": preset, "layout_seed": seed, "elements": elements,
            "lighting": {"sun_pitch": light.uniform(-65, -25), "sun_yaw": light.uniform(-170, 170),
                         "intensity": light.uniform(30000, 60000), "exposure": 12.0}}


def scene_recipe(template, preset, layout_seed, identity):
    recipe = copy.deepcopy(template)
    recipe["environment"] = environment(preset, layout_seed)
    base = "/Game/Argus/PCG/Datasets/" + identity
    recipe.update(map_path=base + "/Map", graph_path=base + "/Graph")
    recipe["seed"] = 0  # Actual PCG seed is supplied separately for each capture.
    branches = recipe["branches"]
    if preset == "park":
        for b in branches:
            if b["semantic_class"] == "tree":
                b.update(count=5, spacing=1250, scale=0.8)
                b["center"] = [-2100 if b["id"].endswith("west") else 2100, -700, 20]
            elif b["semantic_class"] in ("bench", "prop"):
                b["center"][0] = 3250 if b["semantic_class"] == "bench" else 3650
                b["center"][1] = -1500
    elif preset == "yard":
        recipe["branches"] = [b for b in branches if b["semantic_class"] in ("vehicle", "prop")]
        recipe["branches"][0].update(count=5, spacing=1350)
        recipe["branches"][1]["center"] = [1500, -2000, 20]
    else:
        # Keep props in the open strip, clear of all possible building footprints.
        for b in branches:
            if b["semantic_class"] in ("bench", "prop"):
                b["center"][0] = 1650
                b["center"][1] = -500 if b["semantic_class"] == "bench" else 0
    return recipe


def sample_poses(group, camera_seed, count):
    rng = random.Random(derived_seed("camera", group, camera_seed))
    poses = []
    for i in range(count):
        stratum = ("ground", "oblique", "topdown")[i % 3]
        if stratum == "ground":
            side = -1 if (i // 3) % 2 == 0 else 1
            x, y, z = rng.uniform(-160, 160), side * rng.uniform(3100, 3600), rng.uniform(200, 320)
            target = [rng.uniform(-100, 100), -side * 600, 170]
        elif stratum == "oblique":
            theta = rng.uniform(0, 2 * math.pi)
            radius = rng.uniform(6800, 7800)
            x, y, z = radius * math.cos(theta), radius * math.sin(theta), rng.uniform(4800, 6800)
            target = [rng.uniform(-500, 500), rng.uniform(-500, 500), 300]
        else:
            x, y, z = rng.uniform(-1200, 1200), rng.uniform(-1200, 1200), rng.uniform(11000, 13000)
            target = [x, y, 0]
        dx, dy, dz = (target[0] - x, target[1] - y, target[2] - z)
        poses.append(dict(id=f"{stratum}_{i:05d}", stratum=stratum, x=x, y=y, z=z,
                          pitch=math.degrees(math.atan2(dz, math.hypot(dx, dy))),
                          yaw=math.degrees(math.atan2(dy, dx)), roll=0,
                          fov=rng.uniform(60, 85)))
    return poses


def camera_calibration(pose, width, height):
    """UE SceneCapture horizontal FOV, square pixels, no custom projection.

    K uses pixel-edge coordinates (pixel centres at n+0.5). Subtract 0.5 from
    cx/cy when using a library whose first pixel centre is (0,0).
    camera_to_world columns are optical right, down, forward in UE world axes.
    """
    pitch, yaw = map(math.radians, (pose["pitch"], pose["yaw"]))
    cp, sp, cy, sy = math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    if pose["roll"] != 0:
        raise ValueError("Dataset cameras require roll=0")
    f = width / (2 * math.tan(math.radians(pose["fov"]) / 2))
    right, down, forward = [-sy, cy, 0], [sp * cy, sp * sy, -cp], [cp * cy, cp * sy, sp]
    return {"K": [[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]],
            "pixel_coordinates": "edges; centres at (u+0.5,v+0.5)", "world_units": "cm",
            "world_axes": "UE left-handed X forward Y right Z up",
            "camera_axes": "optical X right Y down Z forward",
            "camera_to_world": [[right[j], down[j], forward[j], pose[k]]
                                for j, k in enumerate(("x", "y", "z"))] + [[0, 0, 0, 1]]}


def verify_artifacts(directory, manifest):
    directory = Path(directory).resolve()
    if not manifest:
        return False
    for name, digest in manifest.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or sha256_file(path) != digest:
            return False
    return True
