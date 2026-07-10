"""Stable CSV readers shared by backends and applications."""

import csv

from .parsing import parse_float, parse_int


def read_semantic_classes(csv_path):
    classes = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            semantic_class = str(row.get("semantic_class", "")).strip()
            if not semantic_class:
                continue
            classes.append(
                {
                    "semantic_class": semantic_class,
                    "stencil": parse_int(row.get("stencil"), default=0),
                    "color_r": parse_float(row.get("color_r"), default=0.0),
                    "color_g": parse_float(row.get("color_g"), default=0.0),
                    "color_b": parse_float(row.get("color_b"), default=0.0),
                }
            )
    return classes


def semantic_map_to_stencil(csv_path):
    mapping = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            actor_name = str(row.get("actor_name", "")).strip()
            component_name = str(row.get("component_name", "")).strip()
            if not actor_name or not component_name:
                continue

            entry = {
                "actor_name": actor_name,
                "component_name": component_name,
                "semantic_class": str(row.get("semantic_class", "")).strip(),
                "render_main_pass": str(row.get("render_main_pass", "")).strip(),
                "render_custom_depth": str(row.get("render_custom_depth", "")).strip(),
                "stencil": parse_int(row.get("stencil"), default=None),
                "mesh_name": str(row.get("mesh_name", "")).strip(),
                "mesh_path": str(row.get("mesh_path", "")).strip(),
                "material_name": str(row.get("material_name", "")).strip(),
                "material_path": str(row.get("material_path", "")).strip(),
                "material_slot": str(row.get("material_slot", "")).strip(),
                "instance_index": parse_int(row.get("instance_index"), default=None),
            }
            for key, value in row.items():
                if key not in entry:
                    entry[key] = value
            mapping.append(entry)
    return mapping


def read_pose_rows(csv_path):
    poses = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            poses.append(
                {
                    "id": row.get("id") or "pose_{:06d}".format(index),
                    "x": parse_float(row.get("x"), 0.0),
                    "y": parse_float(row.get("y"), 0.0),
                    "z": parse_float(row.get("z"), 0.0),
                    "pitch": parse_float(row.get("pitch"), 0.0),
                    "yaw": parse_float(row.get("yaw"), 0.0),
                    "roll": parse_float(row.get("roll"), 0.0),
                    "fov": parse_float(row.get("fov"), None),
                    "fx_px": parse_float(row.get("fx_px"), None),
                    "fy_px": parse_float(row.get("fy_px"), None),
                    "cx_px": parse_float(row.get("cx_px"), None),
                    "cy_px": parse_float(row.get("cy_px"), None),
                    "sensor_width_mm": parse_float(row.get("sensor_width_mm"), None),
                    "sensor_height_mm": parse_float(row.get("sensor_height_mm"), None),
                    "projection_type": str(row.get("projection_type", "")).strip(),
                    "ortho_width": parse_float(row.get("ortho_width"), None),
                }
            )
    return poses
