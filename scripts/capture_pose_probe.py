"""Run one Argus capture from an explicit pose config inside UE Python."""

import json
import os
import sys
from datetime import datetime


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "capture_probe_pose.json")
POSE_KEYS = ("x", "y", "z", "pitch", "yaw", "roll")


def load_probe_config(path=None):
    """Load a probe capture config and normalize the pose to floats."""
    path = str(path or DEFAULT_CONFIG_PATH)

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    pose = raw.get("pose", {})
    normalized_pose = {}

    for key in POSE_KEYS:
        if key not in pose:
            raise ValueError("capture probe pose is missing '{}'".format(key))

        normalized_pose[key] = float(pose[key])

    return {
        "capture_id_prefix": str(raw.get("capture_id_prefix", "pose_probe")).strip()
        or "pose_probe",
        "pose": normalized_pose,
    }


def _prepare_import_paths():
    for path in (PROJECT_ROOT, SCRIPT_DIR):
        if path not in sys.path:
            sys.path.insert(0, path)


def _clear_argus_modules():
    for module_name in list(sys.modules):
        if (
            module_name == "capture_rgb_and_mask"
            or module_name == "scripts.capture_rgb_and_mask"
            or module_name == "setup_dual_capture"
            or module_name == "scripts.setup_dual_capture"
            or
            module_name == "common"
            or module_name == "argus_components"
            or module_name.startswith("argus_components.")
            or module_name == "argus_core.capture"
            or module_name.startswith("argus_core.capture.")
            or module_name == "argus_core.semantics"
            or module_name.startswith("argus_core.semantics.")
        ):
            sys.modules.pop(module_name, None)


def main(config_path=None):
    _prepare_import_paths()
    _clear_argus_modules()

    cfg = load_probe_config(config_path)
    capture_id = "{}_{}".format(
        cfg["capture_id_prefix"],
        datetime.now().strftime("%Y%m%d_%H%M%S"),
    )

    from capture_rgb_and_mask import capture_once

    result = capture_once(capture_id=capture_id, pose=cfg["pose"])
    print("ARGUS_PROBE_RESULT={}".format(json.dumps(result, ensure_ascii=False)))
    return result


if __name__ == "__main__":
    main()
