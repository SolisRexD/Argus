"""UE editor: serial scene creation, PCG generation and multi-view capture.

run(plan_path) returns immediately. Inspect done/error/phase on later ticks.
Resume verifies plan/source and completed artifact hashes; failed scenes get a
new attempt folder. Only maps created by this plan are saved automatically.
"""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import unreal
import build_pcg_scene
import capture_pcg_scene
from argus_backends.ue.dataset_scene import DatasetSceneBuilder, validate_camera
from argus_components.pcg_generation import ensure_idle
from argus_core.dataset import atomic_json, sha256_file, verify_artifacts
from plan_dataset import load_plan

_active = None


class DatasetRun:
    def __init__(self, plan_path):
        self.path = Path(plan_path).resolve()
        self.directory = self.path.parent
        self.plan = load_plan(self.path)
        self.state_path = self.directory / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {
            "plan_sha256": self.plan["plan_sha256"], "scenes": {}, "assets": {}}
        if self.state["plan_sha256"] != self.plan["plan_sha256"]:
            raise ValueError("Checkpoint belongs to a different plan")
        self.done, self.error = False, None
        self.phase = "created"
        self.success_count = self.skipped_count = 0
        self.index, self._tick_handle, self._capture = 0, None, None

    def start(self):
        global _active
        ensure_idle()
        if _active and not _active.done:
            raise RuntimeError("A dataset batch is already active")
        self._check_editor()
        _active = self
        self.phase = "next_scene"
        self._tick_handle = unreal.register_slate_post_tick_callback(self._tick)
        self._write()
        return self

    def _check_editor(self):
        if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world():
            raise RuntimeError("Dataset batch requires editor mode; stop PIE first")
        if unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():
            raise RuntimeError("Save/review the modified map before starting or switching scenes")

    def _write(self):
        self.state.update(phase=self.phase, error=str(self.error) if self.error else None,
                          success_count=self.success_count, skipped_count=self.skipped_count)
        atomic_json(self.state_path, self.state)

    def _asset_hashes(self, scene):
        content = Path(unreal.Paths.project_content_dir())
        return {scene["recipe"][k]: sha256_file(content / (scene["recipe"][k][len("/Game/"):] + ext))
                for k, ext in (("map_path", ".umap"), ("graph_path", ".uasset"))}

    def _tick(self, delta):
        if self.done or self.phase != "next_scene":
            return
        # UE asset/map operations can pump Slate recursively on this same thread.
        # Set the barrier before calling any editor API, not just before capture.
        self.phase = "preparing_scene"
        try:
            if self.index == len(self.plan["scenes"]):
                self._finish()
                return
            scene = self.plan["scenes"][self.index]
            key = scene["key"]
            previous = self.state["scenes"].get(key)
            if previous and previous["phase"] == "captured" and verify_artifacts(
                    self.directory / previous["attempt"], previous["artifacts"]):
                self.skipped_count += 1
                self.index += 1
                self.phase = "next_scene"
                self._write()
                return
            ensure_idle()
            self._check_editor()
            level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
            map_path = scene["recipe"]["map_path"]
            config_path = self.directory / "configs" / (key + ".json")
            if unreal.EditorAssetLibrary.does_asset_exist(map_path):
                if self._asset_hashes(scene) != self.state["assets"].get(scene["group"]):
                    raise RuntimeError("Scene assets changed or have no checkpoint; preserve them and create a new plan")
                if not level.load_level(map_path):
                    raise RuntimeError("Could not load dataset map")
            else:
                builder = DatasetSceneBuilder(self.plan["classes"], scene["recipe"]["environment"])
                build_pcg_scene.create(str(config_path), builder=builder)
                self.state["assets"][scene["group"]] = self._asset_hashes(scene)
            attempt = "attempts/" + key + "_" + uuid.uuid4().hex[:12]
            self.state["scenes"][key] = {"phase": "capturing", "attempt": attempt}
            self.phase = "capturing:" + key
            self._write()
            self._capture = capture_pcg_scene.run(
                scene["pcg_seed"], str(config_path), poses=scene["poses"], mode="editor",
                directory=self.directory / attempt, validate_pose=validate_camera)
            self._capture.add_done_callback(self._captured)
        except Exception as exc:
            self._finish(exc)

    def _captured(self, capture):
        try:
            if capture.error:
                raise capture.error
            scene = self.plan["scenes"][self.index]
            if capture.success_count != len(scene["poses"]):
                raise RuntimeError("Incomplete scene capture")
            level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
            world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
            if world.get_path_name().split(".")[0] != scene["recipe"]["map_path"]:
                raise RuntimeError("World changed during dataset capture")
            if not level.save_current_level():
                raise RuntimeError("Could not save owned dataset map")
            self.state["assets"][scene["group"]] = self._asset_hashes(scene)
            entry = self.state["scenes"][scene["key"]]
            entry.update(phase="captured", scene_sha256=capture.result["scene_sha256"],
                         artifacts={p.relative_to(capture.directory).as_posix(): sha256_file(p)
                                    for p in capture.directory.rglob("*") if p.is_file()})
            self.success_count += 1
            self.index += 1
            self._capture = None
            self.phase = "next_scene"  # Map changes happen on a later tick, outside capture callbacks.
            self._write()
        except Exception as exc:
            self._finish(exc)

    def _finish(self, error=None):
        global _active
        if self.done:
            return
        self.error = error.with_traceback(None) if error else None
        self.phase = "failed" if error else "captured"
        if self._tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self._tick_handle)
            self._tick_handle = None
        self._capture = None
        self.done = True
        if _active is self:
            _active = None
        self._write()
        print("ARGUS_DATASET", self.phase, self.success_count, self.skipped_count, str(error or ""))


def run(plan_path):
    return DatasetRun(plan_path).start()
