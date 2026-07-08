"""
Argus 公共工具函数模块。

本模块提供整个 Argus 管线共用的基础能力：

1. 配置文件读取
2. 路径解析
3. 目录创建
4. 日志输出
5. UE Editor Actor 查询
6. SceneCaptureComponent2D 获取
7. UE 资产加载
8. 基础类型解析
9. semantic_classes.csv 读取
10. semantic_map.csv 读取
11. camera_poses.csv 读取

注意：
- 本模块应尽量保持稳定。
- 其他脚本和组件会大量依赖这里的函数名与返回格式。
"""

import csv
import json
import os
from datetime import datetime

import unreal


def _script_dir():
    """
    获取当前脚本所在目录。

    在普通 Python 执行环境中：
    - 使用 __file__ 获取脚本路径。

    在 UE Python Console 或某些特殊执行环境中：
    - __file__ 可能不存在。
    - 此时回退到当前工作目录 os.getcwd()。
    """
    if "__file__" in globals():
        return os.path.dirname(os.path.abspath(__file__))

    return os.getcwd()


def get_project_root():
    """
    获取 Argus 项目根目录。

    默认假设当前脚本位于：

        <project_root>/scripts/

    因此项目根目录为 scripts 的上一级。
    """
    return os.path.abspath(os.path.join(_script_dir(), ".."))


def resolve_path(path_str, project_root=None):
    """
    将配置中的路径解析为绝对路径。

    规则：
    - 如果 path_str 为空，返回空字符串。
    - 如果 path_str 已经是绝对路径，直接规范化后返回。
    - 如果 path_str 是相对路径，则相对于 project_root 解析。
    - 如果没有传入 project_root，则使用 get_project_root()。
    """
    if not path_str:
        return ""

    if os.path.isabs(path_str):
        return os.path.normpath(path_str)

    root = project_root or get_project_root()

    return os.path.normpath(os.path.join(root, path_str))


def load_json_config(config_path=None):
    """
    读取 pipeline_config.json。

    参数：
    - config_path:
        可选配置文件路径。
        如果为空，则默认读取：

            <project_root>/config/pipeline_config.json

    返回：
    - data:
        配置字典。
    - abs_path:
        解析后的配置文件绝对路径。
    """
    root = get_project_root()
    path = config_path or os.path.join(root, "config", "pipeline_config.json")
    abs_path = resolve_path(path, root)

    with open(abs_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data, abs_path


def ensure_dir(path_str):
    """
    确保目录存在。

    如果 path_str 为空字符串，则直接跳过。
    这样可以兼容只传文件名、不带目录的情况，例如：

        metadata.csv
    """
    if not path_str:
        return

    os.makedirs(path_str, exist_ok=True)


def now_stamp():
    """
    返回紧凑时间戳。

    用途：
    - 文件名
    - capture_id 默认后缀
    - metadata 时间字段
    """
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg):
    """输出 Argus 普通日志。"""
    unreal.log("[Argus] {}".format(msg))


def warn(msg):
    """输出 Argus 警告日志。"""
    unreal.log_warning("[Argus] {}".format(msg))


def err(msg):
    """输出 Argus 错误日志。"""
    unreal.log_error("[Argus] {}".format(msg))


def make_rotator(pitch, yaw, roll):
    """Create a UE rotator from explicit pitch/yaw/roll values."""
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
    """
    获取 UE EditorActorSubsystem。

    该子系统用于：
    - 获取当前关卡 Actor
    - 创建 Actor
    - 查询选中 Actor 等编辑器操作
    """
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def get_all_level_actors():
    """
    获取当前关卡中的所有 Actor。

    注意：
    - 这是 Editor 环境下的 Actor 查询。
    - 需要在 UE 编辑器 Python 环境中运行。
    """
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

    for existing in worlds:
        if existing is world:
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
    """
    Enumerate actors through GameplayStatics so lookup also works during PIE.

    EditorActorSubsystem rejects some calls while a play session is active. The
    gameplay path works against the duplicated PIE world and avoids editor-only
    assumptions in capture code.
    """
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
    """
    Keep utility actors available in PIE even when World Partition streams cells.

    CitySample can place newly spawned editor actors into spatially loaded cells.
    SceneCapture rigs must be present before the player/camera is moved, so they
    need to live outside spatial streaming.
    """
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
    """
    根据 Actor Label 查找 Actor。

    参数：
    - label:
        UE 编辑器中显示的 Actor Label。

    返回：
    - 找到则返回 Actor。
    - 找不到则返回 None。
    """
    actor = _find_actor_in_list(get_all_world_actors(), label)

    if actor:
        return actor

    try:
        actor = _find_actor_in_list(get_all_level_actors(), label)

        if actor:
            return actor
    except Exception:
        pass

    return None


def get_capture_component(actor):
    """
    从 Actor 上获取 SceneCaptureComponent2D。

    返回：
    - 找到则返回 SceneCaptureComponent2D。
    - 找不到或 actor 为空则返回 None。
    """
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

    candidate_paths = [asset_path, _asset_object_path(asset_path)]

    for path in candidate_paths:
        for loader in loaders:
            try:
                asset = loader(path)
            except Exception:
                asset = None

            if asset:
                return asset

    return None


def load_asset_or_raise(asset_path):
    """
    加载 UE 资产。

    参数：
    - asset_path:
        UE 资产路径，例如：

            /Game/Argus/RT_RGB

    返回：
    - 加载成功的 UE 资产对象。

    如果加载失败，抛出 RuntimeError。
    """
    asset = _try_load_asset(asset_path)

    if not asset:
        raise RuntimeError("加载 UE 资产失败: {}".format(asset_path))

    return asset


def choose_capture_source(name):
    """
    解析 SceneCaptureSource 枚举。

    由于不同 UE 版本中的枚举名称可能略有差异，
    这里会先尝试用户配置的 name，
    如果失败，再尝试一组常见 fallback。

    参数：
    - name:
        配置中的 SceneCaptureSource 名称。

    返回：
    - unreal.SceneCaptureSource 中的枚举值。
    """
    if name:
        try:
            return getattr(unreal.SceneCaptureSource, name)
        except Exception:
            pass

    # 不同 UE 版本枚举名可能不同，按顺序尝试。
    fallbacks = [
        "SCS_FINAL_COLOR_LDR",
        "SCS_FINAL_TONE_CURVE_HDR",
        "SCS_FINAL_COLOR_HDR",
    ]

    for candidate in fallbacks:
        try:
            return getattr(unreal.SceneCaptureSource, candidate)
        except Exception:
            pass

    raise RuntimeError("当前 UE 版本中找不到可用的 SceneCaptureSource 枚举")


def parse_bool(v, default=False):
    """
    解析常见布尔值写法。

    支持 true：
    - True
    - 1
    - true
    - yes
    - y
    - on

    支持 false：
    - False
    - 0
    - false
    - no
    - n
    - off

    如果值为空或无法识别，则返回 default。
    """
    if v is None:
        return default

    if isinstance(v, bool):
        return v

    text = str(v).strip().lower()

    if text == "":
        return default

    if text in {"1", "true", "yes", "y", "on"}:
        return True

    if text in {"0", "false", "no", "n", "off"}:
        return False

    return default


def parse_int(v, default=None):
    """
    安全解析整数。

    如果值为空或解析失败，则返回 default。
    """
    try:
        if v is None or str(v).strip() == "":
            return default

        return int(v)
    except Exception:
        return default


def parse_float(v, default=None):
    """
    安全解析浮点数。

    如果值为空或解析失败，则返回 default。
    """
    try:
        if v is None or str(v).strip() == "":
            return default

        return float(v)
    except Exception:
        return default


def read_semantic_classes(csv_path):
    """
    读取 semantic_classes.csv。

    该文件用于生成后处理材质中的 stencil -> color 映射。

    期望字段：
    - semantic_class
    - stencil
    - color_r
    - color_g
    - color_b

    返回：
    - classes: list[dict]
    """
    classes = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            semantic_class = str(row.get("semantic_class", "")).strip()

            if not semantic_class:
                continue

            classes.append(
                {
                    "semantic_class": semantic_class,
                    "stencil": parse_int(row.get("stencil"), default=0),
                    "color_r": parse_float(row.get("color_r"), default=0.0),
                    "color_g": parse_float(row.get("color_g"), default=0.0),
                    "color_b": parse_float(row.get("color_b"), default=0.0),
                }
            )

    return classes


def normalize_color_255_to_1(v):
    """
    将颜色值归一化到 [0, 1]。

    支持两种输入：
    - 0-255 范围，例如 128
    - 0-1 范围，例如 0.5

    返回：
    - 范围限制在 [0, 1] 的 float。
    """
    fv = float(v)

    if fv > 1.0:
        return max(0.0, min(1.0, fv / 255.0))

    return max(0.0, min(1.0, fv))


def semantic_map_to_stencil(csv_path):
    """
    读取 semantic_map.csv。

    semantic_map.csv 是 LLM / 人工清洗后的语义映射表，
    用于后续写回 UE 组件属性。

    主要字段：
    - actor_name
    - component_name
    - semantic_class
    - render_main_pass
    - render_custom_depth
    - stencil
    - mesh_name
    - mesh_path
    - material_name
    - material_path
    - material_slot
    - instance_index

    额外字段会被保留，方便诊断和日志输出。
    """
    mapping = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            actor_name = str(row.get("actor_name", "")).strip()
            component_name = str(row.get("component_name", "")).strip()
            semantic_class = str(row.get("semantic_class", "")).strip()
            stencil = parse_int(row.get("stencil"), default=None)

            # 没有 actor/component 的行无法回写，直接跳过。
            if not actor_name or not component_name:
                continue

            entry = {
                "actor_name": actor_name,
                "component_name": component_name,
                "semantic_class": semantic_class,
                "render_main_pass": str(row.get("render_main_pass", "")).strip(),
                "render_custom_depth": str(row.get("render_custom_depth", "")).strip(),
                "stencil": stencil,
                "mesh_name": str(row.get("mesh_name", "")).strip(),
                "mesh_path": str(row.get("mesh_path", "")).strip(),
                "material_name": str(row.get("material_name", "")).strip(),
                "material_path": str(row.get("material_path", "")).strip(),
                "material_slot": str(row.get("material_slot", "")).strip(),
                "instance_index": parse_int(row.get("instance_index"), default=None),
            }

            # 保留 CSV 中其他额外字段，例如：
            # - confidence
            # - reason
            # - review_status
            # - notes
            for k, v in row.items():
                if k not in entry:
                    entry[k] = v

            mapping.append(entry)

    return mapping


def read_pose_rows(csv_path):
    """
    读取 camera_poses.csv。

    支持基础位姿字段：
    - id
    - x
    - y
    - z
    - pitch
    - yaw
    - roll

    支持可选逐帧相机内参字段：
    - fov
    - fx_px
    - fy_px
    - cx_px
    - cy_px
    - sensor_width_mm
    - sensor_height_mm
    - projection_type
    - ortho_width

    返回：
    - poses: list[dict]
    """
    poses = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for i, row in enumerate(reader, start=1):
            poses.append(
                {
                    "id": row.get("id") or "pose_{:06d}".format(i),
                    "x": parse_float(row.get("x"), 0.0),
                    "y": parse_float(row.get("y"), 0.0),
                    "z": parse_float(row.get("z"), 0.0),
                    "pitch": parse_float(row.get("pitch"), 0.0),
                    "yaw": parse_float(row.get("yaw"), 0.0),
                    "roll": parse_float(row.get("roll"), 0.0),
                    "fov": parse_float(row.get("fov"), None),
                    "fx_px": parse_float(row.get("fx_px"), None),
                    "fy_px": parse_float(row.get("fy_px"), None),
                    "cx_px": parse_float(row.get("cx_px"), None),
                    "cy_px": parse_float(row.get("cy_px"), None),
                    "sensor_width_mm": parse_float(row.get("sensor_width_mm"), None),
                    "sensor_height_mm": parse_float(row.get("sensor_height_mm"), None),
                    "projection_type": str(row.get("projection_type", "")).strip(),
                    "ortho_width": parse_float(row.get("ortho_width"), None),
                }
            )

    return poses
