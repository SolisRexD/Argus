import csv
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from argus_core.dataset import (camera_calibration, class_mapping, environment, sample_poses,
                                scene_recipe, scene_split, sha256_file, validate_spec, verify_artifacts)
from argus_core.dataset_quality import inspect_pair
from argus_core.pcg import validate_recipe

ROOT = Path(__file__).resolve().parents[1]


def test_diverse_reproducible_environments_and_independent_camera_seed():
    for preset in ("urban", "park", "yard"):
        assert environment(preset, 42) == environment(preset, 42)
        assert environment(preset, 42) != environment(preset, 43)
        poses = sample_poses(preset, 1, 6)
        assert poses == sample_poses(preset, 1, 6)
        assert poses != sample_poses(preset, 2, 6)
        assert {p["stratum"] for p in poses} == {"ground", "oblique", "topdown"}
    assert len({json.dumps(environment(p, 42)) for p in ("urban", "park", "yard")}) == 3


def test_all_compiled_recipes_have_unambiguous_stencils():
    template = json.loads((ROOT / "config/pcg_lab_recipe.json").read_text())
    with (ROOT / "config/semantic_classes.csv").open() as stream:
        classes = class_mapping(csv.DictReader(stream))
    for preset in ("urban", "park", "yard"):
        for seed in (-1, 42, 43):
            validate_recipe(scene_recipe(template, preset, seed, "test"), classes)


@pytest.mark.parametrize("key,value", [("layout_seeds", [1, 1]), ("camera_seed", True),
    ("views_per_scene", 0), ("environments", ["spaceship"]), ("resolution", [0, 1080]),
    ("split_ratios", [1, 1, 1]), ("warmup_seconds", float("nan"))])
def test_invalid_specs_fail_before_authoring(key, value):
    spec = json.loads((ROOT / "config/dataset_pcg.json").read_text())
    spec[key] = value
    with pytest.raises(ValueError):
        validate_spec(spec)


def test_class_ids_must_be_unique_even_when_colors_collide():
    with pytest.raises(ValueError, match="unique"):
        class_mapping([{"semantic_class": "background", "stencil": 0},
                       {"semantic_class": "ignore", "stencil": 0}])


def test_plan_keeps_views_and_pcg_variants_together_and_freezes_inputs(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from plan_dataset import create, load_plan
    spec = json.loads((ROOT / "config/dataset_pcg.json").read_text())
    spec["pcg_seeds"] = [42, 43]
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    path = create(spec_path, tmp_path / "dataset")
    plan = load_plan(path)
    for group in {s["group"] for s in plan["scenes"]}:
        variants = [s for s in plan["scenes"] if s["group"] == group]
        assert len(variants) == 2
        assert variants[0]["split"] == variants[1]["split"]
        assert variants[0]["poses"] == variants[1]["poses"]
        assert variants[0]["recipe"]["map_path"] == variants[1]["recipe"]["map_path"]
    config = next((path.parent / "configs").glob("*.json"))
    config.write_text("{}")
    with pytest.raises(ValueError, match="recipe/config changed"):
        load_plan(path)
    with pytest.raises(FileExistsError):
        create(spec_path, path.parent)


def test_calibration_projects_camera_axes():
    p = dict(x=10, y=20, z=30, pitch=0, yaw=0, roll=0, fov=90)
    c = camera_calibration(p, 1920, 1080)
    matrix = np.array(c["camera_to_world"])
    assert np.allclose(matrix @ [0, 0, 100, 1], [110, 20, 30, 1])
    assert np.allclose(matrix @ [100, 0, 0, 1], [10, 120, 30, 1])
    assert np.allclose(matrix @ [0, 100, 0, 1], [10, 20, -70, 1])
    assert np.allclose(c["K"], [[960, 0, 960], [0, 960, 540], [0, 0, 1]])


def test_artifact_resume_checks_content_and_rejects_path_escape(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"first")
    manifest = {image.name: sha256_file(image)}
    assert verify_artifacts(tmp_path, manifest)
    image.write_bytes(b"other")
    assert not verify_artifacts(tmp_path, manifest)
    assert not verify_artifacts(tmp_path, {"../frame.png": "bad"})


def test_quality_rejects_corrupt_labels_and_accepts_exact_ids(tmp_path):
    rgb = np.full((10, 10, 4), 255, dtype=np.uint8)
    rgb[:5, :, :3] = 80
    mask = rgb.copy()
    mask[:5, :, :3] = 2
    mask[5:, :, :3] = 5
    r, m = tmp_path / "rgb.png", tmp_path / "mask.png"
    Image.fromarray(rgb).save(r)
    Image.fromarray(mask).save(m)
    assert inspect_pair(r, m, [10, 10], {0, 2, 5})["accepted"]
    mask[0, 0, :3] = [253, 1, 253]
    Image.fromarray(mask).save(m)
    result = inspect_pair(r, m, [10, 10], {0, 2, 5})
    assert not result["accepted"]
    assert "unexpected_class_ids" in result["errors"] and "mask_channels_differ" in result["errors"]
    m.write_bytes(b"not png")
    with pytest.raises(Exception):
        inspect_pair(r, m, [10, 10], {0, 2, 5})
