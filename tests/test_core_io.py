import csv
import json

from argus_core.io import (
    load_json_config,
    parse_bool,
    parse_float,
    parse_int,
    read_pose_rows,
    read_semantic_classes,
    resolve_path,
    semantic_map_to_stencil,
)


def test_primitive_parsers_preserve_legacy_defaults():
    assert parse_bool("yes") is True
    assert parse_bool("off", default=True) is False
    assert parse_bool("unexpected", default=True) is True
    assert parse_int("17") == 17
    assert parse_int("", default=9) == 9
    assert parse_float("2.5") == 2.5
    assert parse_float(None, default=1.25) == 1.25


def test_resolve_path_and_load_json_config_accept_explicit_project_root(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "pipeline_config.json"
    config_path.write_text(json.dumps({"capture": {"width": 640}}), encoding="utf-8")

    resolved = resolve_path("config/pipeline_config.json", project_root=str(tmp_path))
    data, loaded_path = load_json_config(project_root=str(tmp_path))

    assert resolved == str(config_path)
    assert loaded_path == str(config_path)
    assert data == {"capture": {"width": 640}}


def test_read_semantic_classes_normalizes_numbers_and_skips_empty_names(tmp_path):
    csv_path = tmp_path / "semantic_classes.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["semantic_class", "stencil", "color_r", "color_g", "color_b"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "semantic_class": "road",
                "stencil": "2",
                "color_r": "10",
                "color_g": "20.5",
                "color_b": "30",
            }
        )
        writer.writerow({"semantic_class": "", "stencil": "99"})

    assert read_semantic_classes(str(csv_path)) == [
        {
            "semantic_class": "road",
            "stencil": 2,
            "color_r": 10.0,
            "color_g": 20.5,
            "color_b": 30.0,
        }
    ]


def test_semantic_map_preserves_review_fields_and_skips_unresolvable_rows(tmp_path):
    csv_path = tmp_path / "semantic_map.csv"
    fieldnames = [
        "actor_name",
        "component_name",
        "semantic_class",
        "stencil",
        "instance_index",
        "confidence",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "actor_name": "Road_01",
                "component_name": "Mesh",
                "semantic_class": "road",
                "stencil": "2",
                "instance_index": "3",
                "confidence": "0.95",
            }
        )
        writer.writerow(
            {
                "actor_name": "",
                "component_name": "Mesh",
                "semantic_class": "road",
                "stencil": "2",
            }
        )

    rows = semantic_map_to_stencil(str(csv_path))

    assert len(rows) == 1
    assert rows[0]["actor_name"] == "Road_01"
    assert rows[0]["stencil"] == 2
    assert rows[0]["instance_index"] == 3
    assert rows[0]["confidence"] == "0.95"


def test_read_pose_rows_applies_stable_defaults(tmp_path):
    csv_path = tmp_path / "poses.csv"
    csv_path.write_text(
        "id,x,y,z,pitch,yaw,roll,fov,projection_type\n"
        ",1.5,-2,300,-90,0,5,,orthographic\n",
        encoding="utf-8",
    )

    assert read_pose_rows(str(csv_path)) == [
        {
            "id": "pose_000001",
            "x": 1.5,
            "y": -2.0,
            "z": 300.0,
            "pitch": -90.0,
            "yaw": 0.0,
            "roll": 5.0,
            "fov": None,
            "fx_px": None,
            "fy_px": None,
            "cx_px": None,
            "cy_px": None,
            "sensor_width_mm": None,
            "sensor_height_mm": None,
            "projection_type": "orthographic",
            "ortho_width": None,
        }
    ]
