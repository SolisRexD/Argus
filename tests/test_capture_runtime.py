import json
from pathlib import Path

from argus_core.capture import (
    DEFAULT_ALLOWED_CONSOLE_PREFIXES,
    build_runtime_preparation_plan,
    is_console_command_allowed,
)


def test_runtime_preparation_is_disabled_by_default():
    plan = build_runtime_preparation_plan({}, pose=None)

    assert plan.enabled is False
    assert plan.console_commands == ()
    assert plan.warmup_seconds == 0.0
    assert plan.pause_after_warmup is False


def test_project_pipeline_config_enables_citysample_fastgeo_semantic_capture():
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config" / "pipeline_config.json").read_text())

    plan = build_runtime_preparation_plan(cfg, pose={"x": 1, "y": 2, "z": 3})

    assert "FastGeo.EnableTransformer 0" in plan.console_commands
    assert plan.post_capture_console_commands == ("FastGeo.EnableTransformer 1",)
    assert cfg["runtime"]["auto_semantic_stencil"]["max_components"] >= 60000


def test_citysample_bigcity_profile_generates_world_partition_commands():
    cfg = {
        "runtime": {
            "enabled": True,
            "profile": "citysample_bigcity",
            "warmup_seconds": 2.5,
            "pause_after_warmup": True,
            "resume_after_capture": True,
            "citysample": {
                "main_grid_loading_range": 25600,
                "hlod0_loading_range": 96000,
                "hlod_warmup_enabled": False,
            },
        }
    }

    plan = build_runtime_preparation_plan(
        cfg,
        pose={"x": 10, "y": 20, "z": 300, "pitch": -15, "yaw": 90, "roll": 0},
    )

    assert plan.enabled is True
    assert plan.profile == "citysample_bigcity"
    assert plan.capture_point.x == 10.0
    assert plan.capture_point.y == 20.0
    assert plan.capture_point.z == 300.0
    assert plan.warmup_seconds == 2.5
    assert plan.pause_after_warmup is True
    assert plan.resume_after_capture is True
    assert plan.console_commands == (
        "wp.Runtime.OverrideRuntimeSpatialHashLoadingRange -grid=0 -range=25600",
        "wp.Runtime.OverrideRuntimeSpatialHashLoadingRange -grid=1 -range=96000",
        "wp.Runtime.HLOD.WarmupEnabled 0",
    )
    assert plan.move_player_to_capture is True
    assert plan.restore_player_after_capture is True


def test_citysample_runtime_plan_can_disable_and_restore_fastgeo_transformer():
    cfg = {
        "runtime": {
            "enabled": True,
            "profile": "citysample_bigcity",
            "citysample": {
                "disable_fastgeo_transformer_for_semantic_capture": True,
                "restore_fastgeo_transformer_after_capture": True,
            },
        }
    }

    plan = build_runtime_preparation_plan(cfg, pose={"x": 1, "y": 2, "z": 3})

    assert plan.console_commands[0] == "FastGeo.EnableTransformer 0"
    assert "wp.Runtime.OverrideRuntimeSpatialHashLoadingRange -grid=0 -range=12800" in (
        plan.console_commands
    )
    assert plan.post_capture_console_commands == ("FastGeo.EnableTransformer 1",)
    assert plan.rejected_console_commands == ()
    assert plan.rejected_post_capture_console_commands == ()


def test_runtime_plan_allows_disabling_player_streaming_source_move():
    cfg = {
        "runtime": {
            "enabled": True,
            "profile": "citysample_bigcity",
            "move_player_to_capture": False,
            "restore_player_after_capture": False,
        }
    }

    plan = build_runtime_preparation_plan(cfg, pose={"x": 1, "y": 2, "z": 3})

    assert plan.move_player_to_capture is False
    assert plan.restore_player_after_capture is False


def test_citysample_runtime_plan_hides_and_offsets_player_streaming_source():
    cfg = {
        "runtime": {
            "enabled": True,
            "profile": "citysample_bigcity",
        }
    }

    plan = build_runtime_preparation_plan(cfg, pose={"x": 1, "y": 2, "z": 3})

    assert plan.hide_player_during_capture is True
    assert plan.player_streaming_source_z_offset_cm == 5000.0


def test_runtime_plan_filters_non_allowlisted_extra_console_commands():
    cfg = {
        "runtime": {
            "enabled": True,
            "profile": "generic",
            "console_commands": [
                "r.Streaming.PoolSize 4096",
                "quit",
                "open /Game/Map/Other",
            ],
        }
    }

    plan = build_runtime_preparation_plan(cfg)

    assert plan.console_commands == ("r.Streaming.PoolSize 4096",)
    assert plan.rejected_console_commands == ("quit", "open /Game/Map/Other")


def test_console_command_allowlist_matches_command_prefix_only():
    assert is_console_command_allowed(
        "wp.Runtime.HLOD.WarmupEnabled 1",
        DEFAULT_ALLOWED_CONSOLE_PREFIXES,
    )
    assert is_console_command_allowed(
        "FastGeo.EnableTransformer 0",
        DEFAULT_ALLOWED_CONSOLE_PREFIXES,
    )
    assert not is_console_command_allowed(
        "quit",
        DEFAULT_ALLOWED_CONSOLE_PREFIXES,
    )
