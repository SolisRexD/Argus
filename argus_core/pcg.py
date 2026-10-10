"""Pure validation and stable content identities for the CPU PCG lab."""
import hashlib
import json
import math
import re


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def validate_recipe(recipe, classes):
    if recipe.get("schema_version") != 1:
        raise ValueError("Unsupported PCG recipe version")
    for name in ("map_path", "graph_path"):
        if not str(recipe.get(name, "")).startswith("/Game/Argus/PCG/"):
            raise ValueError(name + " must be under /Game/Argus/PCG/")
    if not recipe.get("actor_label"):
        raise ValueError("Missing PCG actor_label")
    if not recipe.get("branches"):
        raise ValueError("PCG recipe needs branches")
    seen = set()
    for branch in recipe["branches"]:
        name = branch["id"]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in seen:
            raise ValueError("Invalid or duplicate branch ID: " + name)
        seen.add(name)
        if branch["semantic_class"] not in classes:
            raise ValueError("Unknown semantic class: " + branch["semantic_class"])
        stencil = classes[branch["semantic_class"]]
        if type(stencil) is not int or not 0 <= stencil <= 255:
            raise ValueError("Stencil must be an integer in [0, 255]")
        if not str(branch["mesh"]).startswith(("/Game/", "/Engine/")):
            raise ValueError("Expected a mesh asset path")
        if type(branch["count"]) is not int or not 1 <= branch["count"] <= 1000:
            raise ValueError("Branch count must be an integer in [1, 1000]")
        for field in ("spacing", "scale"):
            if not math.isfinite(branch[field]) or branch[field] <= 0:
                raise ValueError("Invalid " + field)
        for field, length in (("center", 3), ("jitter", 3), ("yaw", 2)):
            if len(branch[field]) != length or not all(math.isfinite(x) for x in branch[field]):
                raise ValueError("Invalid " + field)
        if any(x < 0 for x in branch["jitter"]) or branch["yaw"][0] > branch["yaw"][1]:
            raise ValueError("Invalid jitter or yaw interval")
        if branch["jitter"][1] * 2 >= branch["spacing"]:
            raise ValueError("Jitter can cross neighboring grid cells")
    validate_seed(recipe["seed"])
    if not math.isfinite(recipe["timeout_seconds"]) or recipe["timeout_seconds"] <= 0:
        raise ValueError("Invalid generation timeout")
    return recipe


def validate_seed(seed):
    if type(seed) is not int or not -(2 ** 31) <= seed < 2 ** 31:
        raise ValueError("PCG seed must be a signed 32-bit integer")
    return seed


def canonical_instances(rows):
    """Exclude volatile UE names/indices; preserve all realized mesh transforms."""
    return sorted(({k: row[k] for k in
                    ("source_rule", "semantic_class", "stencil", "mesh", "transform")}
                   for row in rows), key=lambda row: (row["source_rule"], fingerprint(row)))


def validate_instances(recipe, classes, rows):
    rules = {b["id"]: b for b in recipe["branches"]}
    counts = dict.fromkeys(rules, 0)
    seen = set()
    for row in rows:
        rule = rules.get(row["source_rule"])
        if not rule:
            raise ValueError("Unexpected PCG source rule")
        expected = (rule["semantic_class"], classes[rule["semantic_class"]], rule["mesh"])
        actual = (row["semantic_class"], row["stencil"], row["mesh"])
        if actual != expected or not row["custom_depth"]:
            raise ValueError("PCG semantic descriptor mismatch: " + rule["id"])
        if len(row["transform"]) != 10 or not all(math.isfinite(x) for x in row["transform"]):
            raise ValueError("Non-finite PCG transform")
        identity = (row["source_rule"], tuple(row["transform"]))
        if identity in seen:
            raise ValueError("Duplicate PCG instance within a source rule")
        seen.add(identity)
        counts[rule["id"]] += 1
    if counts != {b["id"]: b["count"] for b in recipe["branches"]}:
        raise ValueError("Incomplete PCG generation: " + str(counts))
    return counts
