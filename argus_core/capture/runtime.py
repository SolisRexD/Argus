"""Runtime preparation planning for large streamed capture worlds."""

from dataclasses import dataclass


DEFAULT_ALLOWED_CONSOLE_PREFIXES = (
    "CitySample.",
    "FastGeo.",
    "wp.Runtime.",
    "r.Streaming.",
    "r.Nanite.",
    "sg.",
    "MassTraffic.",
    "Crowd.",
    "ai.mass.scalability.",
)


CITYSAMPLE_PROFILES = {
    "citysample",
    "citysample_bigcity",
    "citysample_big_city",
    "bigcity",
    "big_city",
}


@dataclass(frozen=True)
class CapturePoint:
    """Capture viewpoint used by runtime streaming and warmup logic."""

    x: float
    y: float
    z: float
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0

    def to_metadata(self):
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "pitch": self.pitch,
            "yaw": self.yaw,
            "roll": self.roll,
        }


@dataclass(frozen=True)
class RuntimePreparationPlan:
    """Pure data plan describing how a backend should prepare runtime capture."""

    enabled: bool
    profile: str = "generic"
    capture_point: CapturePoint | None = None
    warmup_seconds: float = 0.0
    pause_after_warmup: bool = False
    resume_after_capture: bool = False
    wait_for_streaming: bool = True
    move_player_to_capture: bool = False
    restore_player_after_capture: bool = False
    hide_player_during_capture: bool = False
    player_streaming_source_z_offset_cm: float = 0.0
    console_commands: tuple[str, ...] = ()
    rejected_console_commands: tuple[str, ...] = ()
    post_capture_console_commands: tuple[str, ...] = ()
    rejected_post_capture_console_commands: tuple[str, ...] = ()

    def to_metadata(self):
        return {
            "enabled": self.enabled,
            "profile": self.profile,
            "capture_point": self.capture_point.to_metadata() if self.capture_point else None,
            "warmup_seconds": self.warmup_seconds,
            "pause_after_warmup": self.pause_after_warmup,
            "resume_after_capture": self.resume_after_capture,
            "wait_for_streaming": self.wait_for_streaming,
            "move_player_to_capture": self.move_player_to_capture,
            "restore_player_after_capture": self.restore_player_after_capture,
            "hide_player_during_capture": self.hide_player_during_capture,
            "player_streaming_source_z_offset_cm": self.player_streaming_source_z_offset_cm,
            "console_commands": list(self.console_commands),
            "rejected_console_commands": list(self.rejected_console_commands),
            "post_capture_console_commands": list(self.post_capture_console_commands),
            "rejected_post_capture_console_commands": list(
                self.rejected_post_capture_console_commands
            ),
        }


@dataclass(frozen=True)
class RuntimePlaySessionPlan:
    """Commands that must run outside PIE at play-session boundaries."""

    enabled: bool
    profile: str = "generic"
    pre_play_console_commands: tuple[str, ...] = ()
    rejected_pre_play_console_commands: tuple[str, ...] = ()
    post_play_console_commands: tuple[str, ...] = ()
    rejected_post_play_console_commands: tuple[str, ...] = ()

    def to_metadata(self):
        return {
            "enabled": self.enabled,
            "profile": self.profile,
            "pre_play_console_commands": list(self.pre_play_console_commands),
            "rejected_pre_play_console_commands": list(
                self.rejected_pre_play_console_commands
            ),
            "post_play_console_commands": list(self.post_play_console_commands),
            "rejected_post_play_console_commands": list(
                self.rejected_post_play_console_commands
            ),
        }


def build_runtime_play_session_plan(cfg):
    """Build commands that must execute before PIE starts and after it stops."""
    runtime_cfg = dict((cfg or {}).get("runtime", {}) or {})
    enabled = _parse_bool(runtime_cfg.get("enabled"), default=False)

    if not enabled:
        return RuntimePlaySessionPlan(enabled=False)

    profile = _normalize_profile(runtime_cfg.get("profile", "generic"))
    allowed_prefixes = _tuple_text(
        runtime_cfg.get("allowed_console_prefixes"),
        default=DEFAULT_ALLOWED_CONSOLE_PREFIXES,
    )
    trusted_pre_play_commands = []
    trusted_post_play_commands = []

    if profile in CITYSAMPLE_PROFILES:
        city_pre_play, city_post_play = _citysample_play_session_commands(runtime_cfg)
        trusted_pre_play_commands.extend(city_pre_play)
        trusted_post_play_commands.extend(city_post_play)

    extra_pre_accepted, pre_rejected = _filter_console_commands(
        _tuple_text(runtime_cfg.get("pre_play_console_commands"), default=()),
        allowed_prefixes,
    )
    extra_post_accepted, post_rejected = _filter_console_commands(
        _tuple_text(runtime_cfg.get("post_play_console_commands"), default=()),
        allowed_prefixes,
    )
    pre_accepted = _dedupe_console_commands(
        trusted_pre_play_commands + extra_pre_accepted
    )
    post_accepted = _dedupe_console_commands(
        trusted_post_play_commands + extra_post_accepted
    )

    return RuntimePlaySessionPlan(
        enabled=True,
        profile=profile,
        pre_play_console_commands=tuple(pre_accepted),
        rejected_pre_play_console_commands=tuple(pre_rejected),
        post_play_console_commands=tuple(post_accepted),
        rejected_post_play_console_commands=tuple(post_rejected),
    )


def build_runtime_preparation_plan(cfg, pose=None):
    """Build a backend-neutral runtime preparation plan from pipeline config."""
    runtime_cfg = dict((cfg or {}).get("runtime", {}) or {})
    enabled = _parse_bool(runtime_cfg.get("enabled"), default=False)

    if not enabled:
        return RuntimePreparationPlan(enabled=False)

    profile = _normalize_profile(runtime_cfg.get("profile", "generic"))
    allowed_prefixes = _tuple_text(
        runtime_cfg.get("allowed_console_prefixes"),
        default=DEFAULT_ALLOWED_CONSOLE_PREFIXES,
    )

    trusted_commands = []
    trusted_post_capture_commands = []

    if profile in CITYSAMPLE_PROFILES:
        city_commands, city_post_capture_commands = _citysample_commands(runtime_cfg)
        trusted_commands.extend(city_commands)
        trusted_post_capture_commands.extend(city_post_capture_commands)

    extra_accepted, rejected = _filter_console_commands(
        _tuple_text(runtime_cfg.get("console_commands"), default=()),
        allowed_prefixes,
    )
    extra_post_accepted, post_rejected = _filter_console_commands(
        _tuple_text(runtime_cfg.get("post_capture_console_commands"), default=()),
        allowed_prefixes,
    )
    accepted = _dedupe_console_commands(trusted_commands + extra_accepted)
    post_accepted = _dedupe_console_commands(
        trusted_post_capture_commands + extra_post_accepted
    )
    should_move_player = _parse_bool(
        runtime_cfg.get("move_player_to_capture"),
        default=profile in CITYSAMPLE_PROFILES,
    )
    default_z_offset = 5000.0 if should_move_player and profile in CITYSAMPLE_PROFILES else 0.0

    return RuntimePreparationPlan(
        enabled=True,
        profile=profile,
        capture_point=capture_point_from_pose(pose),
        warmup_seconds=max(0.0, _parse_float(runtime_cfg.get("warmup_seconds"), 0.0)),
        pause_after_warmup=_parse_bool(runtime_cfg.get("pause_after_warmup"), default=False),
        resume_after_capture=_parse_bool(runtime_cfg.get("resume_after_capture"), default=False),
        wait_for_streaming=_parse_bool(runtime_cfg.get("wait_for_streaming"), default=True),
        move_player_to_capture=should_move_player,
        restore_player_after_capture=_parse_bool(
            runtime_cfg.get("restore_player_after_capture"),
            default=(should_move_player and profile not in CITYSAMPLE_PROFILES),
        ),
        hide_player_during_capture=_parse_bool(
            runtime_cfg.get("hide_player_during_capture"),
            default=should_move_player,
        ),
        player_streaming_source_z_offset_cm=_parse_float(
            runtime_cfg.get("player_streaming_source_z_offset_cm"),
            default_z_offset,
        ),
        console_commands=tuple(accepted),
        rejected_console_commands=tuple(rejected),
        post_capture_console_commands=tuple(post_accepted),
        rejected_post_capture_console_commands=tuple(post_rejected),
    )


def capture_point_from_pose(pose):
    """Convert an optional pose row to a capture point."""
    if not pose:
        return None

    return CapturePoint(
        x=_parse_float(pose.get("x"), 0.0),
        y=_parse_float(pose.get("y"), 0.0),
        z=_parse_float(pose.get("z"), 0.0),
        pitch=_parse_float(pose.get("pitch"), 0.0),
        yaw=_parse_float(pose.get("yaw"), 0.0),
        roll=_parse_float(pose.get("roll"), 0.0),
    )


def is_console_command_allowed(command, allowed_prefixes):
    """Return True when a console command starts with an approved prefix."""
    text = str(command or "").strip()
    if not text:
        return False

    lowered = text.lower()
    for prefix in allowed_prefixes or ():
        if lowered.startswith(str(prefix or "").strip().lower()):
            return True

    return False


def _filter_console_commands(commands, allowed_prefixes):
    accepted = []
    rejected = []
    seen = set()

    for raw_command in commands:
        command = str(raw_command or "").strip()
        if not command:
            continue

        if not is_console_command_allowed(command, allowed_prefixes):
            rejected.append(command)
            continue

        if command in seen:
            continue

        accepted.append(command)
        seen.add(command)

    return accepted, rejected


def _dedupe_console_commands(commands):
    accepted = []
    seen = set()
    for raw_command in commands:
        command = str(raw_command or "").strip()
        if not command or command in seen:
            continue
        accepted.append(command)
        seen.add(command)
    return accepted


def _citysample_commands(runtime_cfg):
    city_cfg = dict(runtime_cfg.get("citysample", {}) or {})
    commands = []
    post_capture_commands = []

    main_grid_range = _parse_int(
        city_cfg.get("main_grid_loading_range", runtime_cfg.get("main_grid_loading_range")),
        12800,
    )
    hlod0_range = _parse_int(
        city_cfg.get("hlod0_loading_range", runtime_cfg.get("hlod0_loading_range")),
        76800,
    )
    hlod_warmup = _parse_bool(
        city_cfg.get("hlod_warmup_enabled", runtime_cfg.get("hlod_warmup_enabled")),
        default=True,
    )

    commands.extend(
        [
            "wp.Runtime.OverrideRuntimeSpatialHashLoadingRange -grid=0 -range={}".format(
                main_grid_range
            ),
            "wp.Runtime.OverrideRuntimeSpatialHashLoadingRange -grid=1 -range={}".format(
                hlod0_range
            ),
            "wp.Runtime.HLOD.WarmupEnabled {}".format(1 if hlod_warmup else 0),
        ]
    )

    return commands, post_capture_commands


def _citysample_play_session_commands(runtime_cfg):
    city_cfg = dict(runtime_cfg.get("citysample", {}) or {})
    pre_play_commands = []
    post_play_commands = []

    legacy_disable_fastgeo = _parse_bool(
        city_cfg.get(
            "disable_fastgeo_transformer_for_semantic_capture",
            runtime_cfg.get("disable_fastgeo_transformer_for_semantic_capture"),
        ),
        default=False,
    )
    configured_disable_before_play = _parse_bool(
        city_cfg.get(
            "disable_fastgeo_transformer_before_play",
            runtime_cfg.get("disable_fastgeo_transformer_before_play"),
        ),
        default=False,
    )
    disable_fastgeo_before_play = (
        configured_disable_before_play or legacy_disable_fastgeo
    )
    legacy_restore_fastgeo = _parse_bool(
        city_cfg.get(
            "restore_fastgeo_transformer_after_capture",
            runtime_cfg.get("restore_fastgeo_transformer_after_capture"),
        ),
        default=legacy_disable_fastgeo,
    )
    restore_fastgeo_after_play = _parse_bool(
        city_cfg.get(
            "restore_fastgeo_transformer_after_play",
            runtime_cfg.get("restore_fastgeo_transformer_after_play"),
        ),
        default=(legacy_restore_fastgeo if legacy_disable_fastgeo else disable_fastgeo_before_play),
    )

    if disable_fastgeo_before_play:
        pre_play_commands.append("FastGeo.EnableTransformer 0")

        if restore_fastgeo_after_play:
            post_play_commands.append("FastGeo.EnableTransformer 1")

    return pre_play_commands, post_play_commands


def _normalize_profile(value):
    text = str(value or "generic").strip().lower()
    return text or "generic"


def _tuple_text(value, default):
    if value is None:
        return tuple(default)

    if isinstance(value, str):
        value = [value]

    try:
        return tuple(str(item).strip() for item in value if str(item).strip())
    except Exception:
        return tuple(default)


def _parse_bool(value, default=False):
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()
    if not text:
        return default

    if text in {"1", "true", "yes", "y", "on"}:
        return True

    if text in {"0", "false", "no", "n", "off"}:
        return False

    return default


def _parse_float(value, default):
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


def _parse_int(value, default):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except Exception:
        return default
