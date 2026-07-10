"""Compatibility imports for legacy Argus UE editor scripts.

New engine-independent code should import from :mod:`argus_core.io`. New UE
integration code should import from :mod:`argus_backends.ue.editor`.
"""

from argus_backends.ue.editor import (
    _actor_matches_label,
    _add_unique_world,
    _asset_object_path,
    _find_actor_in_list,
    _get_world_candidates,
    _try_load_asset,
    choose_capture_source,
    err,
    find_actor_by_label,
    get_actor_subsystem,
    get_all_level_actors,
    get_all_world_actors,
    get_capture_component,
    load_asset_or_raise,
    log,
    make_rotator,
    mark_actor_always_loaded_for_world_partition,
    warn,
)
from argus_core.io import (
    ensure_dir,
    get_project_root,
    load_json_config,
    normalize_color_255_to_1,
    now_stamp,
    parse_bool,
    parse_float,
    parse_int,
    read_pose_rows,
    read_semantic_classes,
    resolve_path,
    semantic_map_to_stencil,
)

__all__ = [
    "choose_capture_source",
    "ensure_dir",
    "err",
    "find_actor_by_label",
    "get_actor_subsystem",
    "get_all_level_actors",
    "get_all_world_actors",
    "get_capture_component",
    "get_project_root",
    "load_asset_or_raise",
    "load_json_config",
    "log",
    "make_rotator",
    "mark_actor_always_loaded_for_world_partition",
    "normalize_color_255_to_1",
    "now_stamp",
    "parse_bool",
    "parse_float",
    "parse_int",
    "read_pose_rows",
    "read_semantic_classes",
    "resolve_path",
    "semantic_map_to_stencil",
    "warn",
]
