"""
多路 SceneCapture 创建、配置与单帧采集服务。

本模块负责 Argus 采集管线中的“相机与输出流”部分：

1. 从配置中解析采集流 streams。
2. 创建或复用 SceneCapture2D Actor。
3. 为每一路采集流配置 RenderTarget、CaptureSource 和后处理材质。
4. 同步主采集流与其他采集流的位置、旋转和相机内参。
5. 执行一次多路同步采集。
6. 导出 RGB、MASK 或其他自定义流文件。
7. 返回 metadata 字典，供后续写入 CSV。

说明：
- 半透明材质进入 mask 的问题，不在这里通过“临时换材质”解决。
- 正确方式是在材质中开启 Allow Custom Depth Writes，
  同时组件开启 Render CustomDepth Pass 并写入 Custom Stencil。
"""

import json
import math
import os
import time
from dataclasses import dataclass

import unreal

from argus_core.capture import force_png_alpha_opaque

from .runtime_control import RuntimeCaptureController
from .runtime_session import RuntimePlaySessionController
from .runtime_semantics import RuntimeSemanticStencilController

from common import (
    choose_capture_source,
    ensure_dir,
    find_actor_by_label,
    get_actor_subsystem,
    get_capture_component,
    load_asset_or_raise,
    mark_actor_always_loaded_for_world_partition,
    make_rotator,
    now_stamp,
    parse_bool,
    parse_float,
    resolve_path,
)


@dataclass
class CaptureStreamSpec:
    """
    单路采集流的规范化定义。

    一路 stream 可以表示：
    - rgb：正常 RGB 图像
    - mask：语义分割图
    - depth：深度图
    - normal：法线图
    - debug：调试图
    - 其他自定义输出

    这个类只保存配置，不直接操作 UE 对象。
    """

    name: str
    actor_label: str
    rt_asset_name: str
    file_suffix: str
    apply_post_process: bool
    post_process_material_name: str
    sync_to_primary: bool
    force_png_opaque: bool
    capture_source: str


class CaptureStreamRegistry:
    """
    采集流注册表。

    负责：
    - 读取 cfg["capture"]["streams"]。
    - 将配置项转换为 CaptureStreamSpec。
    - 如果没有配置 streams，则回退到旧版 rgb/mask 双路模式。
    - 检查 stream name 是否重复。
    - 确定 primary stream。
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.assets = cfg["assets"]
        self.capture_cfg = cfg["capture"]
        self.output_cfg = cfg["output"]

    def _build_default_streams(self):
        """
        构建旧版兼容的默认 rgb/mask 双路采集配置。

        默认两路：
        1. rgb：
           - 使用 rgb_actor_label
           - 使用 rt_rgb_name
           - 不挂后处理材质

        2. mask：
           - 使用 mask_actor_label
           - 使用 rt_mask_name
           - 挂语义 mask 后处理材质
           - 默认同步到 rgb
        """
        return [
            CaptureStreamSpec(
                name="rgb",
                actor_label=self.capture_cfg["rgb_actor_label"],
                rt_asset_name=self.assets["rt_rgb_name"],
                file_suffix="rgb",
                apply_post_process=False,
                post_process_material_name="",
                sync_to_primary=False,
                force_png_opaque=bool(self.output_cfg.get("force_png_opaque", False)),
                capture_source=str(self.capture_cfg.get("capture_source", "")).strip(),
            ),
            CaptureStreamSpec(
                name="mask",
                actor_label=self.capture_cfg["mask_actor_label"],
                rt_asset_name=self.assets["rt_mask_name"],
                file_suffix="mask",
                apply_post_process=True,
                post_process_material_name=self.assets["material_name"],
                sync_to_primary=bool(self.capture_cfg.get("sync_mask_to_rgb", True)),
                force_png_opaque=bool(self.output_cfg.get("force_mask_png_opaque", True)),
                capture_source=str(self.capture_cfg.get("capture_source", "")).strip(),
            ),
        ]

    def _build_stream_from_row(self, row):
        """
        从 capture.streams 的单项配置构建一路采集流。

        必填字段：
        - name
        - actor_label
        - rt_asset_name

        可选字段：
        - file_suffix
        - apply_post_process
        - post_process_material_name
        - sync_to_primary
        - force_png_opaque
        - capture_source
        """
        name = str(row.get("name", "")).strip()
        actor_label = str(row.get("actor_label", "")).strip()
        rt_asset_name = str(row.get("rt_asset_name", "")).strip()

        if not name or not actor_label or not rt_asset_name:
            raise RuntimeError("capture.streams 配置项缺少必填字段: name/actor_label/rt_asset_name")

        return CaptureStreamSpec(
            name=name,
            actor_label=actor_label,
            rt_asset_name=rt_asset_name,
            file_suffix=str(row.get("file_suffix", name)).strip() or name,
            apply_post_process=parse_bool(row.get("apply_post_process"), default=False),
            post_process_material_name=str(row.get("post_process_material_name", "")).strip(),
            sync_to_primary=parse_bool(row.get("sync_to_primary"), default=False),
            force_png_opaque=parse_bool(row.get("force_png_opaque"), default=False),
            capture_source=str(row.get("capture_source", self.capture_cfg.get("capture_source", ""))).strip(),
        )

    def list_streams(self):
        """
        返回当前配置的采集流列表。

        如果 cfg["capture"]["streams"] 存在：
        - 使用用户自定义 streams。

        如果不存在：
        - 使用默认 rgb/mask 双路采集配置。
        """
        configured = self.capture_cfg.get("streams", [])

        if not configured:
            return self._build_default_streams()

        streams = [self._build_stream_from_row(row) for row in configured]
        names = [s.name for s in streams]

        if len(names) != len(set(names)):
            raise RuntimeError("capture.streams 中存在重复的 stream name")

        return streams

    def get_primary_stream(self, streams):
        """
        获取主采集流。

        主采集流用于：
        - 接收 pose 位姿。
        - 作为其他同步流的位置和旋转参考。
        - 作为相机内参参考。

        默认 primary_stream 为 rgb。
        如果配置中的 primary_stream 不存在，则回退到 streams[0]。
        """
        primary_name = str(self.capture_cfg.get("primary_stream", "rgb")).strip()

        for stream in streams:
            if stream.name == primary_name:
                return stream

        if streams:
            return streams[0]

        raise RuntimeError("没有配置任何 capture streams")


class CameraIntrinsicsManager:
    """
    相机内参管理器。

    负责：
    - 从配置中解析相机内参。
    - 从 pose 中读取单帧覆盖内参。
    - 根据 fx 和图像宽度推导水平 FOV。
    - 将内参应用到 SceneCaptureComponent2D。
    - 在多路 SceneCapture 之间同步相机参数。
    """

    def _try_set(self, obj, prop_name, value):
        """
        安全设置 UE 编辑器属性。

        设置成功返回 True。
        设置失败返回 False。
        """
        try:
            obj.set_editor_property(prop_name, value)
            return True
        except Exception:
            return False

    def _try_get(self, obj, prop_name, default=None):
        """
        安全读取 UE 编辑器属性。

        读取失败时返回 default。
        """
        try:
            return obj.get_editor_property(prop_name)
        except Exception:
            return default

    def _to_projection_mode(self, name):
        """
        将配置文本转换为 UE 的 CameraProjectionMode 枚举。

        支持的常见写法：
        - perspective
        - persp
        - orthographic
        - ortho
        """
        text = str(name or "").strip().upper()

        if not text:
            return None

        candidates = [text]

        if text in {"PERSPECTIVE", "PERSP"}:
            candidates.extend(["PERSPECTIVE", "CAMERA_PROJECTION_MODE_PERSPECTIVE"])

        if text in {"ORTHOGRAPHIC", "ORTHO"}:
            candidates.extend(["ORTHOGRAPHIC", "CAMERA_PROJECTION_MODE_ORTHOGRAPHIC"])

        for item in candidates:
            try:
                return getattr(unreal.CameraProjectionMode, item)
            except Exception:
                pass

        return None

    def _read_rt_size(self, rt):
        """
        读取 RenderTarget 尺寸。

        返回：
        - width
        - height

        如果读取失败，返回：
        - None
        - None
        """
        w = self._try_get(rt, "size_x", None)
        h = self._try_get(rt, "size_y", None)

        try:
            return int(w), int(h)
        except Exception:
            return None, None

    def resolve_intrinsics(self, capture_cfg, pose, rt):
        """
        合并并解析相机内参。

        内参来源优先级：
        1. capture.camera_intrinsics
        2. capture.fov 旧版字段
        3. pose CSV 中的单帧覆盖字段

        如果没有 fov_deg，但提供了 fx_px 和 image_width，
        则自动推导水平 FOV：

            fov = 2 * atan(image_width / (2 * fx))
        """
        base = dict(capture_cfg.get("camera_intrinsics", {}))

        # 兼容旧版 capture.fov 字段。
        if "fov_deg" not in base and "fov" in capture_cfg:
            base["fov_deg"] = capture_cfg.get("fov")

        # pose 可以逐帧覆盖内参。
        if pose:
            override_map = {
                "fov": "fov_deg",
                "fov_deg": "fov_deg",
                "fx_px": "fx_px",
                "fy_px": "fy_px",
                "cx_px": "cx_px",
                "cy_px": "cy_px",
                "sensor_width_mm": "sensor_width_mm",
                "sensor_height_mm": "sensor_height_mm",
                "projection_type": "projection_type",
                "ortho_width": "ortho_width",
            }

            for src_key, dst_key in override_map.items():
                value = pose.get(src_key)
                if value not in (None, ""):
                    base[dst_key] = value

        fx = parse_float(base.get("fx_px"), None)
        fov_deg = parse_float(base.get("fov_deg"), None)

        img_w = parse_float(base.get("image_width"), None)
        img_h = parse_float(base.get("image_height"), None)

        # 如果配置中没有图像宽高，则从 RenderTarget 中读取。
        if img_w is None or img_h is None:
            rt_w, rt_h = self._read_rt_size(rt)
            img_w = float(rt_w) if rt_w else img_w
            img_h = float(rt_h) if rt_h else img_h

        # 如果缺少 FOV，但有 fx 和图像宽度，则推导水平 FOV。
        if fov_deg is None and fx and img_w and fx > 0:
            fov_deg = math.degrees(2.0 * math.atan(float(img_w) / (2.0 * float(fx))))

        return {
            "projection_type": str(base.get("projection_type", "")).strip(),
            "fov_deg": fov_deg,
            "fx_px": fx,
            "fy_px": parse_float(base.get("fy_px"), None),
            "cx_px": parse_float(base.get("cx_px"), None),
            "cy_px": parse_float(base.get("cy_px"), None),
            "sensor_width_mm": parse_float(base.get("sensor_width_mm"), None),
            "sensor_height_mm": parse_float(base.get("sensor_height_mm"), None),
            "ortho_width": parse_float(base.get("ortho_width"), None),
            "image_width": img_w,
            "image_height": img_h,
            "use_custom_aspect_ratio": parse_bool(base.get("use_custom_aspect_ratio"), False),
            "aspect_ratio": parse_float(base.get("aspect_ratio"), None),
            "constrain_aspect_ratio": parse_bool(base.get("constrain_aspect_ratio"), False),
        }

    def apply_intrinsics(self, component, intrinsics):
        """
        将解析后的内参应用到 SceneCaptureComponent2D。

        这里使用安全写入。
        某些 UE 版本或组件可能没有个别属性，失败时会跳过。
        """
        mode = self._to_projection_mode(intrinsics.get("projection_type"))

        if mode is not None:
            self._try_set(component, "projection_type", mode)

        if intrinsics.get("fov_deg") is not None:
            self._try_set(component, "fov_angle", float(intrinsics["fov_deg"]))

        if intrinsics.get("ortho_width") is not None:
            self._try_set(component, "ortho_width", float(intrinsics["ortho_width"]))

        if intrinsics.get("aspect_ratio") is not None:
            self._try_set(component, "aspect_ratio", float(intrinsics["aspect_ratio"]))

        self._try_set(
            component,
            "use_custom_aspect_ratio",
            bool(intrinsics.get("use_custom_aspect_ratio", False)),
        )

        self._try_set(
            component,
            "constrain_aspect_ratio",
            bool(intrinsics.get("constrain_aspect_ratio", False)),
        )

    def mirror_camera_intrinsics(self, src_component, dst_component):
        """
        将源 SceneCapture 的相机属性复制到目标 SceneCapture。

        用于保证 RGB、MASK 等多路采集的投影参数一致。
        """
        for key in [
            "projection_type",
            "fov_angle",
            "ortho_width",
            "aspect_ratio",
            "use_custom_aspect_ratio",
            "constrain_aspect_ratio",
        ]:
            value = self._try_get(src_component, key, None)

            if value is not None:
                self._try_set(dst_component, key, value)

    def intrinsics_to_metadata(self, component, intrinsics):
        """
        将内参转换成适合写入 metadata CSV 的字典。
        """
        return {
            "projection_type": str(intrinsics.get("projection_type", "")),
            "fov": self._try_get(component, "fov_angle", intrinsics.get("fov_deg", "")),
            "fx_px": intrinsics.get("fx_px", ""),
            "fy_px": intrinsics.get("fy_px", ""),
            "cx_px": intrinsics.get("cx_px", ""),
            "cy_px": intrinsics.get("cy_px", ""),
            "sensor_width_mm": intrinsics.get("sensor_width_mm", ""),
            "sensor_height_mm": intrinsics.get("sensor_height_mm", ""),
            "ortho_width": intrinsics.get("ortho_width", ""),
            "image_width": intrinsics.get("image_width", ""),
            "image_height": intrinsics.get("image_height", ""),
        }


class BaseCaptureService:
    """
    采集服务基类。

    setup 阶段和 capture 阶段都会用到这些工具函数。
    """

    def _vec(self, value):
        """将三元列表转换为 unreal.Vector。"""
        return unreal.Vector(float(value[0]), float(value[1]), float(value[2]))

    def _rot(self, value):
        """将三元列表转换为 unreal.Rotator。"""
        return make_rotator(float(value[0]), float(value[1]), float(value[2]))

    def _choose_ext_by_rt(self, rt):
        """
        根据 RenderTarget 像素格式选择导出扩展名。

        如果是浮点格式，导出 .hdr。
        否则默认导出 .png。
        """
        try:
            fmt_name = str(rt.get_editor_property("render_target_format")).upper()
        except Exception:
            fmt_name = ""

        if "16F" in fmt_name or "32F" in fmt_name or "FLOAT" in fmt_name:
            return ".hdr"

        return ".png"

    def _export_rt(self, rt, abs_path):
        """
        将 RenderTarget 导出到磁盘。

        abs_path 是完整路径。
        函数内部会拆分出目录和文件名。
        """
        directory = os.path.dirname(abs_path)
        file_name = os.path.basename(abs_path)

        unreal.RenderingLibrary.export_render_target(None, rt, directory, file_name)

    def _capture_twice(self, component):
        """
        连续采集两次。

        目的：
        - 减少第一帧没有完全更新的问题。
        - 降低刚切换位姿、材质或后处理后第一帧异常的概率。
        """
        component.capture_scene()
        component.capture_scene()

    def _configure_component(self, component, rt, capture_source, capture_cfg):
        """
        配置 SceneCaptureComponent2D 的基础属性。

        包括：
        - texture_target
        - capture_source
        - capture_every_frame
        - capture_on_movement
        - always_persist_rendering_state
        """
        component.set_editor_property("texture_target", rt)
        component.set_editor_property("capture_source", choose_capture_source(capture_source))

        for key in ["capture_every_frame", "capture_on_movement", "always_persist_rendering_state"]:
            try:
                component.set_editor_property(key, capture_cfg.get(key, False))
            except Exception:
                pass

    def _clear_post_process_materials(self, scene_capture_comp):
        """Clear stale weighted blendables from a capture component."""
        settings = scene_capture_comp.get_editor_property("post_process_settings")
        weighted = settings.get_editor_property("weighted_blendables")
        weighted.array = []
        settings.set_editor_property("weighted_blendables", weighted)
        scene_capture_comp.set_editor_property("post_process_settings", settings)

    def _set_post_process_material(self, scene_capture_comp, material):
        """Set one full-weight post-process material on a capture component."""
        settings = scene_capture_comp.get_editor_property("post_process_settings")
        weighted = settings.get_editor_property("weighted_blendables")

        blendable = unreal.WeightedBlendable()
        blendable.object = material
        blendable.weight = 1.0
        weighted.array = [blendable]

        settings.set_editor_property("weighted_blendables", weighted)
        scene_capture_comp.set_editor_property("post_process_settings", settings)

    def _configure_stream_post_process(self, component, stream, assets, material_cache):
        """Apply the configured post-process state for one capture stream."""
        self._clear_post_process_materials(component)

        try:
            component.set_editor_property("post_process_blend_weight", 0.0)
        except Exception:
            pass

        if not stream.apply_post_process:
            return

        material_name = stream.post_process_material_name or assets.get(
            "material_name",
            "",
        )
        if not material_name:
            raise RuntimeError(
                "Stream {} requires a post-process material name".format(stream.name)
            )

        if material_name not in material_cache:
            material_cache[material_name] = load_asset_or_raise(
                "{}/{}".format(assets["root"], material_name)
            )

        self._set_post_process_material(component, material_cache[material_name])

        try:
            component.set_editor_property("post_process_blend_weight", 1.0)
        except Exception:
            pass

    def _make_png_opaque(self, path):
        """
        将 PNG 图片的 alpha 通道强制改为 255。

        这个函数只影响导出的 PNG 文件，不影响 UE 场景。

        用途：
        - 避免部分看图软件把透明 alpha 显示成黑色或棋盘格。
        - 让 RGB/mask 文件更容易直接查看。

        如果没有安装 Pillow，则自动跳过。
        """
        if not path.lower().endswith(".png"):
            return

        force_png_alpha_opaque(path)


class DualCaptureSetupService(BaseCaptureService):
    """
    多路 SceneCapture 初始化服务。

    负责：
    - 创建或复用 SceneCapture2D Actor。
    - 配置每一路 RenderTarget。
    - 配置每一路 CaptureSource。
    - 配置 mask 或 debug 流的后处理材质。
    - 同步主流与从流的位置、旋转和相机内参。
    - 保存当前关卡。
    """

    def __init__(self):
        self.intrinsics_manager = CameraIntrinsicsManager()

    def _spawn_scene_capture_if_missing(self, label, location, rotation):
        """
        根据 Actor Label 查找 SceneCapture2D。

        如果存在：
        - 直接复用。

        如果不存在：
        - 在当前关卡中创建新的 SceneCapture2D。
        - 设置 Actor Label。
        """
        actor = find_actor_by_label(label)

        if actor:
            mark_actor_always_loaded_for_world_partition(actor)
            return actor

        actor = get_actor_subsystem().spawn_actor_from_class(
            unreal.SceneCapture2D,
            location,
            rotation,
        )

        if not actor:
            raise RuntimeError("创建 SceneCapture2D 失败: {}".format(label))

        actor.set_actor_label(label)
        mark_actor_always_loaded_for_world_partition(actor)
        return actor

    def _attach_if_possible(self, parent_actor, child_actor):
        """
        尝试把 child_actor 附着到 parent_actor。

        附着失败不是致命错误。
        因为 capture_once 中仍然会主动同步位置和旋转。
        """
        try:
            child_actor.attach_to_actor(
                parent_actor,
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                unreal.AttachmentRule.KEEP_WORLD,
                False,
            )
        except Exception:
            pass

    def setup(self, cfg):
        """
        初始化所有配置的采集流。

        执行流程：
        1. 解析 streams 配置。
        2. 确定 primary stream。
        3. 创建或复用每一路 SceneCapture2D。
        4. 加载每一路 RenderTarget。
        5. 配置每一路 CaptureSource。
        6. 清空旧后处理材质。
        7. 给需要后处理的 stream 设置后处理材质。
        8. 对 primary stream 应用相机内参。
        9. 将 sync_to_primary=true 的 stream 同步到 primary stream。
        10. 保存当前关卡。

        返回：
        - rgb_actor
        - mask_actor

        返回 tuple 是为了兼容旧版 setup_dual_capture.py。
        即使内部已经支持多路 streams，对外仍保留这个接口。
        """
        assets = cfg["assets"]
        capture_cfg = cfg["capture"]

        registry = CaptureStreamRegistry(cfg)
        streams = registry.list_streams()
        primary_stream = registry.get_primary_stream(streams)

        default_loc = self._vec(capture_cfg.get("default_location", [0.0, 0.0, 300.0]))
        default_rot = self._rot(capture_cfg.get("default_rotation", [-20.0, 0.0, 0.0]))

        asset_root = assets["root"]
        material_cache = {}
        stream_states = {}

        for stream in streams:
            actor = self._spawn_scene_capture_if_missing(
                stream.actor_label,
                default_loc,
                default_rot,
            )

            component = get_capture_component(actor)

            if not component:
                raise RuntimeError("SceneCaptureComponent2D 缺失: {}".format(stream.actor_label))

            rt = load_asset_or_raise("{}/{}".format(asset_root, stream.rt_asset_name))
            capture_source_name = stream.capture_source or capture_cfg.get("capture_source", "")

            self._configure_component(component, rt, capture_source_name, capture_cfg)
            self._configure_stream_post_process(
                component,
                stream,
                assets,
                material_cache,
            )

            stream_states[stream.name] = {
                "stream": stream,
                "actor": actor,
                "component": component,
                "rt": rt,
            }

        primary_state = stream_states[primary_stream.name]

        intrinsics = self.intrinsics_manager.resolve_intrinsics(
            capture_cfg,
            pose=None,
            rt=primary_state["rt"],
        )

        self.intrinsics_manager.apply_intrinsics(
            primary_state["component"],
            intrinsics,
        )

        for name, state in stream_states.items():
            if name == primary_stream.name:
                continue

            stream = state["stream"]

            if stream.sync_to_primary:
                state["actor"].set_actor_location_and_rotation(
                    primary_state["actor"].get_actor_location(),
                    primary_state["actor"].get_actor_rotation(),
                    False,
                    True,
                )

                self.intrinsics_manager.mirror_camera_intrinsics(
                    primary_state["component"],
                    state["component"],
                )

                self._attach_if_possible(
                    primary_state["actor"],
                    state["actor"],
                )

            else:
                self.intrinsics_manager.apply_intrinsics(
                    state["component"],
                    intrinsics,
                )

        try:
            unreal.EditorLevelLibrary.save_current_level()
        except Exception:
            pass

        # 保持旧接口：
        # 旧版调用者通常期待 setup() 返回 rgb_actor, mask_actor。
        rgb_actor = stream_states.get("rgb", primary_state)["actor"]
        mask_actor = stream_states.get("mask", primary_state)["actor"]

        return rgb_actor, mask_actor


class CaptureService(BaseCaptureService):
    """
    单帧多路采集服务。

    负责：
    - 查找已经 setup 好的 SceneCapture Actor。
    - 根据 pose 设置主采集流位姿。
    - 同步其他采集流。
    - 应用或同步相机内参。
    - 执行 capture_scene。
    - 导出 RenderTarget。
    - 返回 metadata 字典。
    """

    def __init__(self):
        self.intrinsics_manager = CameraIntrinsicsManager()
        self.runtime_session_controller = RuntimePlaySessionController()
        self.runtime_controller = RuntimeCaptureController()
        self.semantic_stencil_controller = RuntimeSemanticStencilController()

    def _apply_pose_to_actor(self, actor, pose):
        """
        将 pose CSV 的一行位姿应用到 Actor。

        pose 至少应包含：
        - x
        - y
        - z
        - pitch
        - yaw
        - roll
        """
        actor.set_actor_location_and_rotation(
            unreal.Vector(float(pose["x"]), float(pose["y"]), float(pose["z"])),
            make_rotator(float(pose["pitch"]), float(pose["yaw"]), float(pose["roll"])),
            False,
            True,
        )

    def capture_once(self, cfg, capture_id=None, pose=None):
        """
        执行一次多路同步采集，并返回 metadata 字典。

        参数：
        - cfg:
            Argus pipeline 配置。
        - capture_id:
            当前帧 ID。如果为空，则自动生成。
        - pose:
            当前帧相机位姿。如果为空，则使用当前 SceneCapture 位置。

        返回：
        - row:
            可写入 metadata CSV 的一行字典。
        """
        assets = cfg["assets"]
        capture_cfg = cfg["capture"]
        output_cfg = cfg["output"]
        play_session_plan = self.runtime_session_controller.validate_capture_session(cfg)

        out_dir = resolve_path(output_cfg["capture_dir"])
        ensure_dir(out_dir)

        registry = CaptureStreamRegistry(cfg)
        streams = registry.list_streams()
        primary_stream = registry.get_primary_stream(streams)

        asset_root = assets["root"]
        material_cache = {}
        states = {}

        for stream in streams:
            actor = find_actor_by_label(stream.actor_label)

            if not actor:
                raise RuntimeError(
                    "未找到 stream '{}' 对应的 SceneCapture Actor: {}".format(
                        stream.name,
                        stream.actor_label,
                    )
                )

            component = get_capture_component(actor)

            if not component:
                raise RuntimeError(
                    "Actor 上缺少 SceneCaptureComponent2D: {}".format(
                        stream.actor_label
                    )
                )

            rt = load_asset_or_raise("{}/{}".format(asset_root, stream.rt_asset_name))
            capture_source_name = stream.capture_source or capture_cfg.get("capture_source", "")

            self._configure_component(
                component,
                rt,
                capture_source_name,
                capture_cfg,
            )
            self._configure_stream_post_process(
                component,
                stream,
                assets,
                material_cache,
            )

            states[stream.name] = {
                "stream": stream,
                "actor": actor,
                "component": component,
                "rt": rt,
            }

        primary = states[primary_stream.name]

        if pose is not None:
            self._apply_pose_to_actor(primary["actor"], pose)

        intrinsics = self.intrinsics_manager.resolve_intrinsics(
            capture_cfg,
            pose=pose,
            rt=primary["rt"],
        )

        self.intrinsics_manager.apply_intrinsics(
            primary["component"],
            intrinsics,
        )

        for name, state in states.items():
            if name == primary_stream.name:
                continue

            stream = state["stream"]

            if stream.sync_to_primary:
                state["actor"].set_actor_location_and_rotation(
                    primary["actor"].get_actor_location(),
                    primary["actor"].get_actor_rotation(),
                    False,
                    True,
                )

                self.intrinsics_manager.mirror_camera_intrinsics(
                    primary["component"],
                    state["component"],
                )

            else:
                self.intrinsics_manager.apply_intrinsics(
                    state["component"],
                    intrinsics,
                )

        # 采集前等待一小段时间，让位姿、渲染状态或后处理状态稳定。
        runtime_plan = self.runtime_controller.prepare_for_capture(
            cfg,
            pose=pose,
            capture_actor=primary["actor"],
        )
        semantic_stencil_stats = self.semantic_stencil_controller.apply(cfg, pose=pose)

        try:
            time.sleep(max(0.0, float(cfg.get("batch", {}).get("sleep_seconds", 0.0))))

        # 所有 stream 都采集两次，减少首帧不稳定问题。
            for state in states.values():
                self._capture_twice(state["component"])
        finally:
            self.runtime_controller.finish_after_capture(runtime_plan)

        cid = capture_id or "{}_{}".format(
            output_cfg.get("file_prefix", "cap"),
            now_stamp(),
        )

        files = {}

        for name, state in states.items():
            stream = state["stream"]
            ext = self._choose_ext_by_rt(state["rt"])
            suffix = stream.file_suffix or name

            abs_path = os.path.join(
                out_dir,
                "{}_{}{}".format(cid, suffix, ext),
            )

            self._export_rt(state["rt"], abs_path)

            if stream.force_png_opaque:
                self._make_png_opaque(abs_path)

            files[name] = abs_path

        primary_loc = primary["actor"].get_actor_location()
        primary_rot = primary["actor"].get_actor_rotation()

        intrinsics_meta = self.intrinsics_manager.intrinsics_to_metadata(
            primary["component"],
            intrinsics,
        )

        row = {
            "capture_id": cid,
            "x": primary_loc.x,
            "y": primary_loc.y,
            "z": primary_loc.z,
            "pitch": primary_rot.pitch,
            "yaw": primary_rot.yaw,
            "roll": primary_rot.roll,
            "files_json": json.dumps(files, ensure_ascii=False),
            "primary_stream": primary_stream.name,
            "runtime_play_session_plan_json": json.dumps(
                play_session_plan.to_metadata(),
                ensure_ascii=False,
            ),
            "runtime_plan_json": json.dumps(runtime_plan.to_metadata(), ensure_ascii=False),
            "semantic_stencil_json": json.dumps(semantic_stencil_stats, ensure_ascii=False),
            **intrinsics_meta,
        }

        # 兼容旧版字段。
        if "rgb" in files:
            row["rgb_file"] = files["rgb"]

        if "mask" in files:
            row["mask_file"] = files["mask"]

        # 为任意 stream 添加 xxx_file 便捷字段。
        # 例如：
        # - depth_file
        # - normal_file
        # - debug_file
        for name, path in files.items():
            row["{}_file".format(name)] = path

        return row
