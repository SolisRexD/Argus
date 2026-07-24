"""Capture Argus streams from the current PIE player camera."""

import unreal

from capture_rgb_and_mask import capture_with_config
from common import load_json_config


_active_job = None


def _notify(context, message):
    unreal.SystemLibrary.print_string(context, message, True, True)


def _get_player_controller():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world() if subsystem else None
    if not world:
        raise RuntimeError("No PIE world is running")
    controller = unreal.GameplayStatics.get_player_controller(world, 0)
    if not controller:
        raise RuntimeError("No local player controller is available")
    return controller


def _camera_pose(controller):
    camera = controller.get_editor_property("player_camera_manager")
    if not camera:
        raise RuntimeError("No PlayerCameraManager is available")
    location = camera.get_camera_location()
    rotation = camera.get_camera_rotation()
    return {
        "x": float(location.x), "y": float(location.y), "z": float(location.z),
        "pitch": float(rotation.pitch), "yaw": float(rotation.yaw), "roll": float(rotation.roll),
        "fov_deg": float(camera.get_fov_angle()),
    }


def _finish_capture(job, context):
    global _active_job
    if _active_job is job:
        _active_job = None
    if job.error:
        _notify(context, "Argus capture failed: {}".format(job.error))
        return
    capture_id = (job.result or {}).get("capture_id", job.capture_id)
    _notify(context, "Argus captured: {}".format(capture_id))


def capture_player_view():
    global _active_job
    if _active_job is not None and not _active_job.done:
        _notify(None, "Argus capture already in progress")
        return _active_job
    context = None
    job = None
    try:
        context = _get_player_controller()
        cfg, _ = load_json_config()
        runtime_cfg = cfg.setdefault("runtime", {})
        runtime_cfg["move_player_to_capture"] = False
        runtime_cfg["restore_player_after_capture"] = False
        pose = _camera_pose(context)
        _notify(context, "Argus capture started")
        job = capture_with_config(cfg, pose=pose)
        _active_job = job
        job.add_done_callback(lambda completed: _finish_capture(completed, context))
        return job
    except Exception as exc:
        if job is None or job.done:
            _active_job = None
        _notify(context, "Argus capture failed: {}".format(exc))
        raise
