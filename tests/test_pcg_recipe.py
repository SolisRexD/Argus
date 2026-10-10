import copy
import json
from pathlib import Path

import pytest
from argus_core.pcg import canonical_instances, fingerprint, validate_instances, validate_recipe, validate_seed

RECIPE = json.loads((Path(__file__).resolve().parents[1] / "config/pcg_lab_recipe.json").read_text())
CLASSES = {"tree": 15, "vehicle": 6, "bench": 9, "prop": 20}


def instances():
    return [{"source_rule": b["id"], "semantic_class": b["semantic_class"],
             "stencil": CLASSES[b["semantic_class"]], "custom_depth": True,
             "mesh": b["mesh"], "transform": [i, 0, 0, 0, 0, 0, 1, 1, 1, 1],
             "component": "volatile_1", "instance_index": i}
            for b in RECIPE["branches"] for i in range(b["count"])]


def test_valid_recipe_and_complete_generation():
    assert validate_recipe(RECIPE, CLASSES) is RECIPE
    assert sum(validate_instances(RECIPE, CLASSES, instances()).values()) == 18


@pytest.mark.parametrize("mutation", [
    lambda r: r["branches"].append(copy.deepcopy(r["branches"][0])),
    lambda r: r["branches"][0].update(semantic_class="typo"),
    lambda r: r["branches"][0].update(count=0),
    lambda r: r["branches"][0].update(jitter=[0, 9999, 0]),
    lambda r: r["branches"][0].update(center=[float("nan"), 0, 0]),
    lambda r: r.update(map_path="/Game/Map/Big_City_LVL"),
])
def test_invalid_recipe_is_rejected_before_mutation(mutation):
    recipe = copy.deepcopy(RECIPE)
    mutation(recipe)
    with pytest.raises(ValueError):
        validate_recipe(recipe, CLASSES)


@pytest.mark.parametrize("seed", [True, 1.5, 2 ** 31, -(2 ** 31) - 1])
def test_invalid_seed(seed):
    with pytest.raises(ValueError):
        validate_seed(seed)


@pytest.mark.parametrize("field,value", [("stencil", 250), ("custom_depth", False),
                                        ("mesh", "/Engine/BasicShapes/Cube"),
                                        ("source_rule", "missing")])
def test_bad_generated_semantics_rejected(field, value):
    rows = instances()
    rows[0][field] = value
    with pytest.raises(ValueError):
        validate_instances(RECIPE, CLASSES, rows)


def test_missing_and_duplicate_instances_rejected():
    rows = instances()
    for changed in (rows[:-1], rows + [rows[0]]):
        with pytest.raises(ValueError, match="Incomplete|Duplicate"):
            validate_instances(RECIPE, CLASSES, changed)


def test_realized_identity_ignores_iteration_order_and_ue_names():
    rows = instances()
    changed = copy.deepcopy(rows[::-1])
    for row in changed:
        row.update(component="PIE_replaced", instance_index=100)
    assert fingerprint(canonical_instances(rows)) == fingerprint(canonical_instances(changed))
    changed[0]["transform"][0] += 0.001
    assert fingerprint(canonical_instances(rows)) != fingerprint(canonical_instances(changed))
