"""UE 5.8 editor and PIE integration helpers."""

import os

import unreal


def log(msg):
    unreal.log("[Argus] {}".format(msg))


def warn(msg):
    unreal.log_warning("[Argus] {}".format(msg))


def err(msg):
    unreal.log_error("[Argus] {}".format(msg))


def make_rotator(pitch, yaw, roll):
    pitch = float(pitch)
    yaw = float(yaw)
    roll = float(roll)

    try:
        return unreal.Rotator(pitch=pitch, yaw=yaw, roll=roll)
    except Exception:
        rotator = unreal.Rotator()
        rotator.pitch = pitch
        rotator.yaw = yaw
        rotator.roll = roll
        return rotator


def get_actor_subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def get_all_level_actors():
    actors = get_all_world_actors()
    if actors:
        return actors

    try:
        return get_actor_subsystem().get_all_level_actors()
    except Exception:
        return []


def _add_unique_world(worlds, world):
    if not world:
        return
    if any(existing is world for existing in worlds):
        return
    worlds.append(world)


def _get_world_candidates():
    """Return PIE/game world first, then editor world if available."""
    worlds = []

    try:
        _add_unique_world(worlds, unreal.EditorLevelLibrary.get_game_world())
    except Exception:
        pass

    try:
        subsystem_cls = getattr(unreal, "UnrealEditorSubsystem", None)
        if subsystem_cls:
            subsystem = unreal.get_editor_subsystem(subsystem_cls)
            _add_unique_world(worlds, subsystem.get_game_world())
    except Exception:
        pass

    try:
        _add_unique_world(worlds, unreal.EditorLevelLibrary.get_editor_world())
    except Exception:
        pass

    try:
        subsystem_cls = getattr(unreal, "UnrealEditorSubsystem", None)
        if subsystem_cls:
            subsystem = unreal.get_editor_subsystem(subsystem_cls)
            _add_unique_world(worlds, subsystem.get_editor_world())
    except Exception:
        pass

    return worlds


def get_all_world_actors():
    """Enumerate actors through GameplayStatics in editor and PIE worlds."""
    result = []
    seen_ids = set()

    for world in _get_world_candidates():
        try:
            actors = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
        except Exception:
            actors = []

        for actor in actors:
            key = id(actor)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            result.append(actor)

    return result


def mark_actor_always_loaded_for_world_partition(actor):
    """Keep utility actors outside World Partition spatial streaming."""
    if not actor:
        return False

    changed = False
    for prop_name, value in (
        ("is_spatially_loaded", False),
        ("is_editor_only_actor", False),
    ):
        try:
            actor.set_editor_property(prop_name, value)
            changed = True
        except Exception:
            pass
    return changed


def _actor_matches_label(actor, label):
    try:
        if actor and actor.get_actor_label() == label:
            return True
    except Exception:
        pass

    try:
        if actor and actor.get_name() == label:
            return True
    except Exception:
        pass

    return False


def _find_actor_in_list(actors, label):
    for actor in actors:
        if _actor_matches_label(actor, label):
            return actor
    return None


def find_actor_by_label(label):
    actor = _find_actor_in_list(get_all_world_actors(), label)
    if actor:
        return actor

    try:
        return _find_actor_in_list(get_all_level_actors(), label)
    except Exception:
        return None


def get_capture_component(actor):
    if not actor:
        return None
    try:
        return actor.get_component_by_class(unreal.SceneCaptureComponent2D)
    except Exception:
        return None


def _asset_object_path(asset_path):
    clean_path = str(asset_path or "").strip()
    if not clean_path or "." in os.path.basename(clean_path):
        return clean_path
    asset_name = clean_path.rsplit("/", 1)[-1]
    return "{}.{}".format(clean_path, asset_name)


def _try_load_asset(asset_path):
    loaders = []

    runtime_load_asset = getattr(unreal, "load_asset", None)
    if runtime_load_asset:
        loaders.append(lambda path: runtime_load_asset(path))

    runtime_load_object = getattr(unreal, "load_object", None)
    if runtime_load_object:
        loaders.append(lambda path: runtime_load_object(None, path))

    editor_asset_library = getattr(unreal, "EditorAssetLibrary", None)
    if editor_asset_library:
        loaders.append(lambda path: editor_asset_library.load_asset(path))

    for path in (asset_path, _asset_object_path(asset_path)):
        for loader in loaders:
            try:
                asset = loader(path)
            except Exception:
                asset = None
            if asset:
                return asset
    return None


def load_asset_or_raise(asset_path):
    asset = _try_load_asset(asset_path)
    if not asset:
        raise RuntimeError("加载 UE 资产失败: {}".format(asset_path))
    return asset


def choose_capture_source(name):
    if name:
        try:
            return getattr(unreal.SceneCaptureSource, name)
        except Exception:
            pass

    for candidate in (
        "SCS_FINAL_COLOR_LDR",
        "SCS_FINAL_TONE_CURVE_HDR",
        "SCS_FINAL_COLOR_HDR",
    ):
        try:
            return getattr(unreal.SceneCaptureSource, candidate)
        except Exception:
            pass

    raise RuntimeError("当前 UE 版本中找不到可用的 SceneCaptureSource 枚举")
