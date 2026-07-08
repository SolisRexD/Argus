"""Print runtime stencil/custom-depth diagnostics inside UE Python."""

import json
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

for path in (PROJECT_ROOT, SCRIPT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)


import unreal

from argus_components.runtime_semantics import RuntimeSemanticStencilController
from argus_core.semantics import infer_semantic_stencil
from common import _get_world_candidates


INTERESTING_CLASSES = {
    "building",
    "road",
    "terrain",
    "sidewalk",
    "curb_border",
    "vehicle",
    "pedestrian",
    "traffic_sign",
    "prop",
    "unknown",
}

STATIC_CLASSES = {"building", "road", "terrain", "sidewalk", "curb_border"}


def _inc(mapping, key):
    mapping[key] = mapping.get(key, 0) + 1


def _world_name(world):
    try:
        return world.get_path_name()
    except Exception:
        return ""


def _actor_location(actor):
    try:
        loc = actor.get_actor_location()
        return [round(loc.x, 1), round(loc.y, 1), round(loc.z, 1)]
    except Exception:
        return None


def _sample_component(ctrl, actor, component, semantic_class, stencil, custom_depth):
    mesh = ctrl._get_editor_property(component, "static_mesh", None)

    return {
        "class": semantic_class,
        "stencil": stencil,
        "custom_depth": bool(custom_depth),
        "actor": ctrl._actor_label(actor),
        "component": ctrl._object_name(component),
        "loc": _actor_location(actor),
        "mesh": ctrl._object_name(mesh),
    }


def diagnose_world(world, sample_limit=12):
    ctrl = RuntimeSemanticStencilController(actor_provider=lambda: [])

    try:
        actors = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
    except Exception:
        actors = []

    report = {
        "world": _world_name(world),
        "actors": len(actors),
        "components": 0,
        "custom_depth_true": 0,
        "stencil_counts": {},
        "inferred_counts": {},
        "inferred_custom_depth": {},
        "static_samples": [],
    }

    for actor in actors:
        for component in ctrl._get_primitive_components(actor):
            report["components"] += 1

            stencil = ctrl._get_int_property(component, "custom_depth_stencil_value", 0)
            custom_depth = ctrl._get_bool_property(component, "render_custom_depth", False)

            if custom_depth:
                report["custom_depth_true"] += 1

            if stencil > 0:
                _inc(report["stencil_counts"], str(stencil))

            decision = infer_semantic_stencil(
                ctrl._collect_fields(actor, component),
                unknown_for_unmatched=True,
            )
            semantic_class = decision.semantic_class if decision else "ignored"
            _inc(report["inferred_counts"], semantic_class)

            if semantic_class in INTERESTING_CLASSES:
                suffix = "cd" if custom_depth else "no_cd"
                _inc(report["inferred_custom_depth"], "{}:{}".format(semantic_class, suffix))

            if semantic_class in STATIC_CLASSES and len(report["static_samples"]) < sample_limit:
                report["static_samples"].append(
                    _sample_component(
                        ctrl,
                        actor,
                        component,
                        semantic_class,
                        stencil,
                        custom_depth,
                    )
                )

    return report


def main():
    reports = [diagnose_world(world) for world in _get_world_candidates()]

    for report in reports:
        compact = dict(report)
        samples = compact.pop("static_samples", [])
        print("ARGUS_STENCIL_DIAG_WORLD=" + json.dumps(compact, ensure_ascii=False, sort_keys=True))

        for sample in samples:
            print("ARGUS_STENCIL_DIAG_SAMPLE=" + json.dumps(sample, ensure_ascii=False, sort_keys=True))

    return reports


if __name__ == "__main__":
    main()
