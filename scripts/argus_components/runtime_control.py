"""UE runtime preparation helpers for streamed-world capture."""

import time

import unreal

from argus_core.capture import build_runtime_preparation_plan
from common import log, make_rotator, warn


class RuntimeCaptureController:
    """Execute runtime capture preparation plans inside Unreal Editor."""

    def __init__(self, sleep_fn=None):
        self._sleep = sleep_fn or time.sleep
        self._player_restore_state = None

    def prepare_for_capture(self, cfg, pose=None, capture_actor=None):
        """Apply runtime preparation before exporting capture streams."""
        plan = build_runtime_preparation_plan(cfg, pose=pose)

        if not plan.enabled:
            return plan

        world = self._get_world()

        if plan.capture_point:
            log(
                "Runtime prepare at x={:.1f}, y={:.1f}, z={:.1f}".format(
                    plan.capture_point.x,
                    plan.capture_point.y,
                    plan.capture_point.z,
                )
            )

        for command in plan.console_commands:
            self._execute_console_command(world, command)

        for command in plan.rejected_console_commands:
            warn("Rejected runtime console command outside allowlist: {}".format(command))

        if plan.move_player_to_capture:
            self._move_player_streaming_source(
                world,
                plan.capture_point,
                capture_actor=capture_actor,
                restore_after_capture=plan.restore_player_after_capture,
                hide_during_capture=plan.hide_player_during_capture,
                z_offset_cm=plan.player_streaming_source_z_offset_cm,
            )

        if plan.wait_for_streaming:
            self._flush_level_streaming(world)

        if plan.warmup_seconds > 0:
            self._sleep(plan.warmup_seconds)

        if plan.pause_after_warmup:
            self.set_game_paused(True, world=world)

        return plan

    def finish_after_capture(self, plan):
        """Restore runtime state after capture when requested by the plan."""
        if not plan or not plan.enabled:
            return

        world = None
        post_capture_commands = getattr(plan, "post_capture_console_commands", ())
        rejected_post_capture_commands = getattr(
            plan,
            "rejected_post_capture_console_commands",
            (),
        )

        if post_capture_commands or rejected_post_capture_commands:
            world = self._get_world()

        for command in post_capture_commands:
            self._execute_console_command(world, command)

        for command in rejected_post_capture_commands:
            warn("Rejected post-capture console command outside allowlist: {}".format(command))

        if plan.move_player_to_capture and plan.restore_player_after_capture:
            self._restore_player_streaming_source()

        if plan.pause_after_warmup and plan.resume_after_capture:
            self.set_game_paused(False)

    def is_game_paused(self, world=None):
        """Best-effort query for game pause state."""
        world = world or self._get_world()

        if not world:
            return None

        try:
            return bool(unreal.GameplayStatics.is_game_paused(world))
        except Exception as exc:
            warn("Unable to query game pause state: {}".format(exc))
            return None

    def set_game_paused(self, paused, world=None):
        """Best-effort deterministic pause/unpause through GameplayStatics."""
        world = world or self._get_world()

        if not world:
            warn("Unable to set game pause state; no UE world is available")
            return False

        try:
            ok = unreal.GameplayStatics.set_game_paused(world, bool(paused))
            log("Game paused={}".format(bool(paused)))
            return bool(ok)
        except Exception as exc:
            warn("Unable to set game pause state to {}: {}".format(bool(paused), exc))
            return False

    def _execute_console_command(self, world, command):
        try:
            unreal.SystemLibrary.execute_console_command(world, command)
            log("Runtime console command: {}".format(command))
            return True
        except Exception as exc:
            warn("Runtime console command failed '{}': {}".format(command, exc))
            return False

    def _flush_level_streaming(self, world):
        if not world:
            warn("Unable to flush level streaming; no UE world is available")
            return False

        try:
            unreal.GameplayStatics.flush_level_streaming(world)
            log("Requested level streaming flush")
            return True
        except Exception as exc:
            warn("Unable to flush level streaming: {}".format(exc))
            return False

    def _move_player_streaming_source(
        self,
        world,
        capture_point,
        capture_actor=None,
        restore_after_capture=True,
        hide_during_capture=False,
        z_offset_cm=0.0,
    ):
        """Move the active player source so World Partition streams around capture."""
        if not world:
            warn("Unable to move player streaming source; no UE world is available")
            return False

        if not capture_point:
            warn("Unable to move player streaming source; capture pose is missing")
            return False

        pawn = self._get_player_pawn(world)
        controller = self._get_player_controller(world)
        source_actor = pawn or capture_actor

        if not source_actor:
            warn("Unable to move player streaming source; no player pawn or capture actor is available")
            return False

        if restore_after_capture:
            self._player_restore_state = self._snapshot_player_state(
                pawn=pawn,
                controller=controller,
            )
        else:
            self._player_restore_state = None

        location = unreal.Vector(
            capture_point.x,
            capture_point.y,
            capture_point.z + float(z_offset_cm or 0.0),
        )
        rotation = make_rotator(capture_point.pitch, capture_point.yaw, capture_point.roll)

        moved = self._set_actor_transform(source_actor, location, rotation)

        if hide_during_capture and pawn:
            self._set_actor_hidden(pawn, True)

        if controller:
            self._set_controller_rotation(controller, rotation)

            if not pawn and capture_actor:
                self._set_view_target(controller, capture_actor)

        if moved:
            log("Moved player streaming source near capture pose")

        return moved

    def _restore_player_streaming_source(self):
        state = self._player_restore_state
        self._player_restore_state = None

        if not state:
            return False

        pawn = state.get("pawn")
        controller = state.get("controller")

        if pawn:
            self._set_actor_transform(
                pawn,
                state.get("pawn_location"),
                state.get("pawn_rotation"),
            )
            self._set_actor_hidden(pawn, state.get("pawn_hidden"))

        if controller and state.get("control_rotation") is not None:
            self._set_controller_rotation(controller, state.get("control_rotation"))

        if controller and state.get("view_target") is not None:
            self._set_view_target(controller, state.get("view_target"))

        log("Restored player streaming source")
        return True

    def _snapshot_player_state(self, pawn=None, controller=None):
        return {
            "pawn": pawn,
            "pawn_location": self._get_actor_location(pawn),
            "pawn_rotation": self._get_actor_rotation(pawn),
            "pawn_hidden": self._get_actor_hidden(pawn),
            "controller": controller,
            "control_rotation": self._get_controller_rotation(controller),
            "view_target": self._get_view_target(controller),
        }

    def _get_player_pawn(self, world):
        try:
            return unreal.GameplayStatics.get_player_pawn(world, 0)
        except Exception:
            return None

    def _get_player_controller(self, world):
        try:
            return unreal.GameplayStatics.get_player_controller(world, 0)
        except Exception:
            return None

    def _get_actor_location(self, actor):
        if not actor:
            return None

        try:
            return actor.get_actor_location()
        except Exception:
            return None

    def _get_actor_rotation(self, actor):
        if not actor:
            return None

        try:
            return actor.get_actor_rotation()
        except Exception:
            return None

    def _set_actor_transform(self, actor, location, rotation):
        if not actor or location is None or rotation is None:
            return False

        try:
            actor.set_actor_location_and_rotation(location, rotation, False, True)
            return True
        except Exception as exc:
            warn("Unable to move actor for runtime capture: {}".format(exc))
            return False

    def _get_actor_hidden(self, actor):
        if not actor:
            return None

        for method_name in ("is_hidden", "is_actor_hidden_in_game"):
            try:
                method = getattr(actor, method_name)
                return bool(method())
            except Exception:
                pass

        try:
            return bool(actor.get_editor_property("hidden"))
        except Exception:
            return None

    def _set_actor_hidden(self, actor, hidden):
        if not actor or hidden is None:
            return False

        try:
            actor.set_actor_hidden_in_game(bool(hidden))
            return True
        except Exception:
            return False

    def _get_controller_rotation(self, controller):
        if not controller:
            return None

        try:
            return controller.get_control_rotation()
        except Exception:
            return None

    def _set_controller_rotation(self, controller, rotation):
        if not controller or rotation is None:
            return False

        try:
            controller.set_control_rotation(rotation)
            return True
        except Exception:
            return False

    def _get_view_target(self, controller):
        if not controller:
            return None

        try:
            return controller.get_view_target()
        except Exception:
            return None

    def _set_view_target(self, controller, actor):
        if not controller or not actor:
            return False

        try:
            controller.set_view_target(actor)
            return True
        except Exception:
            return False

    def _get_world(self):
        """Prefer PIE/game world, fall back to editor world."""
        for getter in (
            self._get_editor_level_game_world,
            self._get_unreal_editor_game_world,
            self._get_unreal_editor_world,
            self._get_editor_level_world,
        ):
            world = getter()
            if world:
                return world

        return None

    def _get_editor_level_game_world(self):
        try:
            return unreal.EditorLevelLibrary.get_game_world()
        except Exception:
            return None

    def _get_editor_level_world(self):
        try:
            return unreal.EditorLevelLibrary.get_editor_world()
        except Exception:
            return None

    def _get_unreal_editor_game_world(self):
        try:
            subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            return subsystem.get_game_world()
        except Exception:
            return None

    def _get_unreal_editor_world(self):
        try:
            subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            return subsystem.get_editor_world()
        except Exception:
            return None
