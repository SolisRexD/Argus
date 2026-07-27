"""Install or verify the CitySample assets used by Argus Photo Mode capture."""

import argparse
import json
from pathlib import Path

import unreal


ACTION_PATH = "/Game/Input/PhotoMode/IA_PM_ArgusCapture"
SOURCE_ACTION_PATH = "/Game/Input/PhotoMode/IA_PM_AutoFocus"
CONTEXT_PATH = "/Game/Input/PhotoMode/IM_PM_Simple_MappingContext"
BLUEPRINT_PATH = "/Game/Gameplay/Framework/BP_PhotoModeComponent"


def _load_assets():
    action = unreal.EditorAssetLibrary.load_asset(ACTION_PATH)
    context = unreal.EditorAssetLibrary.load_asset(CONTEXT_PATH)
    blueprint_class = unreal.EditorAssetLibrary.load_blueprint_class(BLUEPRINT_PATH)
    if not action or not context or not blueprint_class:
        raise RuntimeError("Argus Photo Mode assets could not be loaded")
    return action, context, unreal.get_default_object(blueprint_class)


def _mapping_action(mapping):
    return mapping.get_editor_property("action")


def _mapping_key(mapping):
    return str(mapping.get_editor_property("key"))


def _f9_mappings(action, context):
    mappings = context.get_editor_property("default_key_mappings").get_editor_property(
        "mappings"
    )
    return [
        mapping
        for mapping in mappings
        if _mapping_action(mapping) == action and _mapping_key(mapping) == "F9"
    ]


def verify_assets():
    action, context, default_object = _load_assets()
    if action.get_editor_property("value_type") != unreal.InputActionValueType.BOOLEAN:
        raise RuntimeError("Argus capture action is not Boolean")
    matches = _f9_mappings(action, context)
    if len(matches) != 1:
        raise RuntimeError("Argus capture action must have exactly one F9 mapping")
    if default_object.get_editor_property("capture_action") != action:
        raise RuntimeError("Photo Mode CDO does not reference the Argus capture action")
    return {"action": ACTION_PATH, "f9_mappings": 1, "capture_action_set": True}


def install_assets():
    if not unreal.EditorAssetLibrary.does_asset_exist(ACTION_PATH):
        if not unreal.EditorAssetLibrary.duplicate_asset(
            SOURCE_ACTION_PATH, ACTION_PATH
        ):
            raise RuntimeError("Argus capture action could not be created")
    action, context, default_object = _load_assets()
    action.set_editor_property("value_type", unreal.InputActionValueType.BOOLEAN)
    key = unreal.Key("F9")
    for _ in _f9_mappings(action, context):
        context.unmap_key(action, key)
    context.map_key(action, key)
    default_object.set_editor_property("capture_action", action)
    for path in (ACTION_PATH, CONTEXT_PATH, BLUEPRINT_PATH):
        if not unreal.EditorAssetLibrary.save_asset(path):
            raise RuntimeError("asset could not be saved: {}".format(path))
    return verify_assets()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("install", "verify"))
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    result_path = Path(args.result)
    result_path.unlink(missing_ok=True)
    details = install_assets() if args.mode == "install" else verify_assets()
    result_path.write_text(
        json.dumps({"ok": True, "details": details}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("ARGUS_CITYSAMPLE_ASSETS_{}_OK".format(args.mode.upper()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
