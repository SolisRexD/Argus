"""Generate, validate, and capture one fixed PCG scene from all configured poses.

Run in PIE. Inspect run.done/run.error/run.result after returning control to UE.
Each run gets a new directory; this MVP deliberately does not resume old seeds.
"""
import copy
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import unreal
from argus_backends.ue.pcg import assert_capture_ready, load_recipe
from argus_components.pcg_generation import PCGGenerationJob, acquire_capture, release_capture
from argus_core.io import read_pose_rows
from argus_core.pcg import fingerprint, validate_seed
from capture_rgb_and_mask import capture_with_config
from common import load_json_config, resolve_path


class PCGCaptureRun:
    def __init__(self, cfg, seed, poses):
        self.cfg = copy.deepcopy(cfg)
        self.seed, self.poses = validate_seed(seed), poses
        self.done, self.error, self.result = False, None, None
        self.success_count, self.failed_count = 0, 0
        self.phase = "created"
        self._job = None
        self._frames = []
        self._callbacks = []
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.directory = ROOT / "output/pcg_lab/runs" / (f"{stamp}_seed{seed}_" + uuid.uuid4().hex[:8])

    def _write(self, name, value):
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")

    def add_done_callback(self, callback):
        if self.done:
            callback(self)
        else:
            self._callbacks.append(callback)
        return self

    def start(self):
        if not unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world():
            raise RuntimeError("Start PIE in the PCG map before capturing")
        if self.cfg["runtime"]["auto_semantic_stencil"]["enabled"]:
            raise ValueError("Disable automatic semantic inference for PCG")
        recipe, classes = load_recipe(self.cfg["pcg"]["recipe"], self.cfg["semantics"]["class_table_csv"])
        self.cfg["output"]["capture_dir"] = str(self.directory / "captures")
        self.cfg["output"]["metadata_csv"] = str(self.directory / "capture_metadata.csv")
        self._write("pipeline_config.json", self.cfg)
        self._write("recipe.json", recipe)
        self._write("classes.json", classes)
        class_table = Path(resolve_path(self.cfg["semantics"]["class_table_csv"]))
        class_bytes = class_table.read_bytes()
        (self.directory / "semantic_classes.csv").write_bytes(class_bytes)
        self._class_table_sha256 = hashlib.sha256(class_bytes).hexdigest()
        self._write("poses.json", self.poses)
        self.phase = "generating"
        try:
            self._job = PCGGenerationJob(recipe, classes, self.seed).start()
            self._job.add_done_callback(self._generated)
        except Exception as exc:
            self._finish(exc)
        return self

    def _generated(self, job):
        try:
            if job.error:
                raise job.error
            acquire_capture(self)
            job.result["class_table_file_sha256"] = self._class_table_sha256
            self.cfg["_pcg_generation"] = job.result
            self._write("generation_manifest.json", job.result)
            self.phase = "capturing"
            self._next()
        except Exception as exc:
            self._finish(exc)

    def _next(self):
        assert_capture_ready(self.cfg)
        if self.success_count == len(self.poses):
            self._finish()
            return
        pose = self.poses[self.success_count]
        capture_id = f"pcg_s{self.seed}_{self.success_count:03d}"
        self._job = capture_with_config(self.cfg, capture_id=capture_id, pose=pose)
        self._job.add_done_callback(self._captured)

    def _captured(self, job):
        try:
            if job.error:
                self.failed_count += 1
                raise job.error
            assert_capture_ready(self.cfg)
            self._frames.append(job.result)
            self.success_count += 1
            self._next()
        except Exception as exc:
            self._finish(exc)

    def _finish(self, error=None):
        if self.done:
            return
        self.error = error.with_traceback(None) if error else None
        self.phase = "failed" if error else "complete"
        self.result = {"phase": self.phase, "error": str(error) if error else None,
                       "seed": self.seed, "success_count": self.success_count,
                       "failed_count": self.failed_count, "frames": self._frames,
                       "scene_sha256": self.cfg.get("_pcg_generation", {}).get("scene_sha256"),
                       "capture_config_sha256": fingerprint({k: v for k, v in self.cfg.items() if k != "_pcg_generation"})}
        try:
            self._write("result.json", self.result)
        finally:
            release_capture(self)
            self._job = None
            self.done = True
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback(self)
            except Exception as exc:
                unreal.log_error("PCG capture callback: " + str(exc))
        print("ARGUS_PCG_CAPTURE", self.phase, str(self.directory), str(error or ""))


def run(seed=None, config_path=str(ROOT / "config/pipeline_pcg.json"), poses=None):
    cfg, _ = load_json_config(config_path)
    recipe, _ = load_recipe(cfg["pcg"]["recipe"], cfg["semantics"]["class_table_csv"])
    poses = read_pose_rows(resolve_path(cfg["batch"]["poses_csv"])) if poses is None else poses
    if not poses:
        raise ValueError("At least one camera pose is required")
    return PCGCaptureRun(cfg, recipe["seed"] if seed is None else seed, poses).start()


if __name__ == "__main__":
    run()
