"""UE adapter for runtime play-session boundary preparation."""

import json
import os

import unreal

from argus_core.capture import RuntimePlaySessionPlan, build_runtime_play_session_plan
from common import ensure_dir, log, resolve_path, warn


DEFAULT_STATE_FILE = "output/runtime_play_session_state.json"
RESTORABLE_BOOL_CVARS = {"FastGeo.EnableTransformer"}


class RuntimePlaySessionController:
    """Apply commands that must run before PIE creation or after PIE teardown."""

    def __init__(self, state_path=None, pid_getter=None):
        self._state_path_override = state_path
        self._pid_getter = pid_getter or os.getpid

    def prepare_before_play(self, cfg):
        plan = build_runtime_play_session_plan(cfg)

        if not plan.enabled:
            return plan

        if self._get_game_world():
            raise RuntimeError("Runtime play-session preparation must run before PIE starts")

        world = self._get_editor_world()
        if not world:
            raise RuntimeError("Runtime play-session preparation requires an editor world")

        state_path = self._get_state_path(cfg)
        existing = self._load_state(state_path)
        if existing:
            if existing.get("editor_pid") != self._pid_getter():
                self._delete_state(state_path)
            elif self._state_matches_plan(existing, plan):
                self._validate_pre_play_effects(plan)
                return plan
            else:
                raise RuntimeError(
                    "A different runtime play session is already prepared in this editor process"
                )

        original_values = self._capture_original_console_values(
            plan.pre_play_console_commands
        )
        try:
            for command in plan.pre_play_console_commands:
                self._execute_console_command(world, command)

            self._validate_pre_play_effects(plan)

            for command in plan.rejected_pre_play_console_commands:
                warn(
                    "Rejected pre-play console command outside allowlist: {}".format(
                        command
                    )
                )

            self._write_state(
                state_path,
                {
                    "editor_pid": self._pid_getter(),
                    "profile": plan.profile,
                    "pre_play_console_commands": list(plan.pre_play_console_commands),
                    "post_play_console_commands": list(plan.post_play_console_commands),
                    "rejected_pre_play_console_commands": list(
                        plan.rejected_pre_play_console_commands
                    ),
                    "rejected_post_play_console_commands": list(
                        plan.rejected_post_play_console_commands
                    ),
                    "original_console_values": original_values,
                },
            )
        except Exception:
            self._restore_original_values_best_effort(world, original_values)
            raise

        return plan

    def validate_capture_session(self, cfg):
        """Fail closed when required pre-PIE settings were not applied."""
        current_plan = build_runtime_play_session_plan(cfg)
        state_path = self._get_state_path(cfg)
        state = self._load_state(state_path)

        if state:
            self._validate_state_process(state)
            plan = self._plan_from_state(state)
        else:
            plan = current_plan
            if not plan.enabled or not plan.pre_play_console_commands:
                return plan
            raise RuntimeError(
                "No matching prepared session; run prepare_runtime_play_session.py before PIE"
            )

        if not self._get_game_world():
            raise RuntimeError("Runtime capture validation requires an active PIE session")

        self._validate_pre_play_effects(plan)
        return plan

    def restore_after_play(self, cfg):
        current_plan = build_runtime_play_session_plan(cfg)

        if self._get_game_world():
            raise RuntimeError("Runtime play-session restore must run after PIE stops")

        world = self._get_editor_world()
        if not world:
            raise RuntimeError("Runtime play-session restore requires an editor world")

        state_path = self._get_state_path(cfg)
        state = self._load_state(state_path)
        if not state:
            if not current_plan.enabled:
                return current_plan
            raise RuntimeError("No prepared session is available to restore")

        self._validate_state_process(state)
        plan = self._plan_from_state(state)
        original_values = dict(state.get("original_console_values", {}) or {})
        restored_values = {}

        for command in plan.post_play_console_commands:
            restore_command = self._restore_command(command, original_values)
            self._execute_console_command(world, restore_command)
            name = self._command_name(restore_command)
            if name in original_values:
                restored_values[name] = original_values[name]

        for command in plan.rejected_post_play_console_commands:
            warn(
                "Rejected post-play console command outside allowlist: {}".format(
                    command
                )
            )

        self._validate_restored_values(restored_values)
        self._delete_state(state_path)

        return plan

    def _execute_console_command(self, world, command):
        try:
            unreal.SystemLibrary.execute_console_command(world, command)
            log("Runtime play-session console command: {}".format(command))
            return True
        except Exception as exc:
            raise RuntimeError(
                "Runtime play-session command failed '{}': {}".format(command, exc)
            ) from exc

    def _get_state_path(self, cfg=None):
        if self._state_path_override:
            return os.path.abspath(self._state_path_override)
        return resolve_path(DEFAULT_STATE_FILE)

    def _capture_original_console_values(self, commands):
        values = {}
        for command in commands:
            name = self._command_name(command)
            if name in RESTORABLE_BOOL_CVARS:
                values[name] = self._read_console_bool(name)
        return values

    def _validate_pre_play_effects(self, plan):
        for command in plan.pre_play_console_commands:
            name = self._command_name(command)
            expected = self._command_bool_value(command)
            if name in RESTORABLE_BOOL_CVARS and expected is not None:
                actual = self._read_console_bool(name)
                if actual is not expected:
                    raise RuntimeError(
                        "Required pre-PIE CVar {}={} was not applied".format(
                            name,
                            int(expected),
                        )
                    )

    def _validate_restored_values(self, values):
        for name, expected in values.items():
            if name not in RESTORABLE_BOOL_CVARS:
                continue
            actual = self._read_console_bool(name)
            if actual is not bool(expected):
                raise RuntimeError(
                    "Runtime play-session restore did not restore {}={}".format(
                        name,
                        int(bool(expected)),
                    )
                )

    def _restore_command(self, command, original_values):
        name = self._command_name(command)
        if name in original_values and name in RESTORABLE_BOOL_CVARS:
            return "{} {}".format(name, int(bool(original_values[name])))
        return command

    def _restore_original_values_best_effort(self, world, values):
        for name, value in values.items():
            if name not in RESTORABLE_BOOL_CVARS:
                continue
            command = "{} {}".format(name, int(bool(value)))
            try:
                self._execute_console_command(world, command)
            except Exception as exc:
                warn("Unable to roll back runtime play-session CVar: {}".format(exc))

    def _read_console_bool(self, name):
        try:
            return bool(unreal.SystemLibrary.get_console_variable_bool_value(name))
        except Exception as exc:
            raise RuntimeError(
                "Unable to read console variable {}: {}".format(name, exc)
            ) from exc

    def _validate_state_process(self, state):
        if state.get("editor_pid") != self._pid_getter():
            raise RuntimeError("Prepared session belongs to a different editor process")

    @staticmethod
    def _plan_from_state(state):
        return RuntimePlaySessionPlan(
            enabled=True,
            profile=state.get("profile", "generic"),
            pre_play_console_commands=tuple(
                state.get("pre_play_console_commands", ())
            ),
            rejected_pre_play_console_commands=tuple(
                state.get("rejected_pre_play_console_commands", ())
            ),
            post_play_console_commands=tuple(
                state.get("post_play_console_commands", ())
            ),
            rejected_post_play_console_commands=tuple(
                state.get("rejected_post_play_console_commands", ())
            ),
        )

    def _state_matches_plan(self, state, plan):
        return (
            state.get("profile") == plan.profile
            and tuple(state.get("pre_play_console_commands", ()))
            == plan.pre_play_console_commands
            and tuple(state.get("post_play_console_commands", ()))
            == plan.post_play_console_commands
        )

    def _load_state(self, path):
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            raise RuntimeError(
                "Unable to read runtime play-session state: {}".format(exc)
            ) from exc

    def _write_state(self, path, state):
        ensure_dir(os.path.dirname(path))
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

    def _delete_state(self, path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _command_name(command):
        parts = str(command or "").strip().split(None, 1)
        return parts[0] if parts else ""

    @staticmethod
    def _command_bool_value(command):
        parts = str(command or "").strip().split(None, 1)
        if len(parts) != 2:
            return None
        value = parts[1].strip().lower()
        if value in ("0", "false"):
            return False
        if value in ("1", "true"):
            return True
        return None

    def _get_game_world(self):
        try:
            subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            return subsystem.get_game_world()
        except Exception:
            pass

        try:
            return unreal.EditorLevelLibrary.get_game_world()
        except Exception:
            return None

    def _get_editor_world(self):
        try:
            subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            return subsystem.get_editor_world()
        except Exception:
            pass

        try:
            return unreal.EditorLevelLibrary.get_editor_world()
        except Exception:
            return None
