"""Lazy public exports for the Unreal Engine backend."""

from importlib import import_module


_EXPORTS = {
    "choose_capture_source": ("editor", "choose_capture_source"),
    "default_annotation_capabilities": (
        "semantics",
        "default_annotation_capabilities",
    ),
    "err": ("editor", "err"),
    "find_actor_by_label": ("editor", "find_actor_by_label"),
    "get_actor_subsystem": ("editor", "get_actor_subsystem"),
    "get_all_level_actors": ("editor", "get_all_level_actors"),
    "get_all_world_actors": ("editor", "get_all_world_actors"),
    "get_capture_component": ("editor", "get_capture_component"),
    "load_asset_or_raise": ("editor", "load_asset_or_raise"),
    "log": ("editor", "log"),
    "make_rotator": ("editor", "make_rotator"),
    "mark_actor_always_loaded_for_world_partition": (
        "editor",
        "mark_actor_always_loaded_for_world_partition",
    ),
    "warn": ("editor", "warn"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc

    value = getattr(import_module(".{}".format(module_name), __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
