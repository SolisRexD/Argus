"""
基于两个渲染开关的语义回写逻辑。

开关说明：
- render_main_pass: 控制主通道（RGB）是否可见
- render_custom_depth: 控制是否参与自定义深度/模板（MASK）
"""

import csv
from dataclasses import dataclass

import unreal

from common import log, parse_int, warn


@dataclass
class SemanticRowContext:
    """规范化的语义规则上下文，用于验证和回写"""

    actor_name: str
    component_name: str
    mesh_name: str
    mesh_path: str
    material_name: str
    material_path: str
    material_slot: str
    instance_index: int
    semantic_class: str
    render_main_pass: bool
    render_custom_depth: bool
    render_main_pass_raw: str
    render_custom_depth_raw: str
    invalid_render_switches: bool
    stencil: int
    ignore_stencil: int
    dry_run: bool = False


class SemanticRuleBuilder:
    """将 CSV 行转换为可执行的渲染开关上下文"""

    def __init__(self, sem_cfg, ignore_stencil):
        self.sem_cfg = sem_cfg
        self.ignore_stencil = int(ignore_stencil)

    def _parse_switch(self, rule, key):
        """
        解析渲染开关值为布尔型。

        支持：
        - true:  1, true, yes, y, on
        - false: 0, false, no, n, off

        返回：
        - parsed_bool
        - raw_string
        - invalid_flag
        """
        raw = str(rule.get(key, "")).strip().lower()

        if raw in {"1", "true", "yes", "y", "on"}:
            return True, raw, False

        if raw in {"0", "false", "no", "n", "off"}:
            return False, raw, False

        return False, raw, True

    def build_context(self, rule):
        """构建上下文并填充计算的模板值默认值"""
        render_main_pass, main_raw, main_invalid = self._parse_switch(rule, "render_main_pass")
        render_custom_depth, depth_raw, depth_invalid = self._parse_switch(rule, "render_custom_depth")

        semantic_class = str(rule.get("semantic_class", "")).strip()
        semantic_class_lower = semantic_class.lower()

        stencil = parse_int(rule.get("stencil"), default=None)
        unknown_stencil = parse_int(self.sem_cfg.get("unknown_stencil"), default=250)

        # 只有进入 mask 的组件才需要有效 stencil。
        # 如果没有写 stencil，就回退 unknown_stencil。
        if render_custom_depth and stencil is None:
            stencil = unknown_stencil

        # ignore 类如果进入 mask，强制使用 ignore_stencil。
        if semantic_class_lower == "ignore" and render_custom_depth:
            stencil = self.ignore_stencil

        return SemanticRowContext(
            actor_name=str(rule.get("actor_name", "")).strip(),
            component_name=str(rule.get("component_name", "")).strip(),
            mesh_name=str(rule.get("mesh_name", "")).strip(),
            mesh_path=str(rule.get("mesh_path", "")).strip(),
            material_name=str(rule.get("material_name", "")).strip(),
            material_path=str(rule.get("material_path", "")).strip(),
            material_slot=str(rule.get("material_slot", "")).strip(),
            instance_index=parse_int(rule.get("instance_index"), default=None),
            semantic_class=semantic_class,
            render_main_pass=render_main_pass,
            render_custom_depth=render_custom_depth,
            render_main_pass_raw=str(rule.get("render_main_pass", "")).strip(),
            render_custom_depth_raw=str(rule.get("render_custom_depth", "")).strip(),
            invalid_render_switches=bool(main_invalid or depth_invalid),
            stencil=stencil,
            ignore_stencil=self.ignore_stencil,
        )


class AnnotationController:
    """应用渲染开关决策到 UE 组件属性"""

    def supports_stencil(self, component):
        """检查组件是否支持模板值属性"""
        try:
            component.get_editor_property("render_custom_depth")
            component.get_editor_property("custom_depth_stencil_value")
            return True
        except Exception:
            return False

    def supports_main_pass(self, component):
        """检查组件是否支持主通道渲染属性"""
        try:
            component.get_editor_property("render_in_main_pass")
            return True
        except Exception:
            return False

    def set_component_main_pass(self, component, enable):
        """
        设置组件主通道可见性。

        对应 UE 属性：
        - render_in_main_pass
        """
        component.set_editor_property("render_in_main_pass", bool(enable))

    def set_component_stencil(self, component, stencil_value, enable=True):
        """
        设置组件自定义深度/模板值。

        注意：
        - 进入 mask 时，不仅要写 stencil，还要打开 render_custom_depth。
        - 否则可能出现 stencil 值写了，但 mask 里仍然没有该物体。
        """
        component.set_editor_property("render_custom_depth", bool(enable))
        component.set_editor_property("custom_depth_stencil_value", int(stencil_value))

    def clear_component_stencil(self, component):
        """
        清除组件自定义深度/模板。

        不进入 mask 的组件应当：
        - render_custom_depth = False
        - custom_depth_stencil_value = 0
        """
        component.set_editor_property("render_custom_depth", False)
        component.set_editor_property("custom_depth_stencil_value", 0)

    def get_component_materials(self, component):
        """获取组件关联的材质列表"""
        try:
            return [m for m in list(component.get_materials()) if m]
        except Exception:
            return []

    def _try_get_editor_prop(self, obj, prop_name, default=None):
        """安全地获取编辑器属性"""
        try:
            return obj.get_editor_property(prop_name)
        except Exception:
            return default

    def _resolve_parent_material(self, material, max_depth=4):
        """
        递归解析材质的父级链条，返回根材质。

        主要用于 MaterialInstance：
        - 实例材质本身可能没有完整 blend_mode 信息
        - 根材质通常更可靠
        """
        current = material

        for _ in range(max_depth):
            parent = self._try_get_editor_prop(current, "parent", None)
            if not parent:
                break
            current = parent

        return current

    def inspect_translucent_material_risk(self, component):
        """
        检查组件的半透明材质是否禁用自定义深度写入。

        如果半透明材质没有开启 Allow Custom Depth Writes，
        即使组件打开了 render_custom_depth，也可能无法正确进入 mask。
        """
        mats = self.get_component_materials(component)
        if not mats:
            return ""

        risky = []

        for material in mats:
            root = self._resolve_parent_material(material)
            blend_mode = self._try_get_editor_prop(root, "blend_mode", None)
            allow_cd = self._try_get_editor_prop(root, "allow_custom_depth_writes", None)

            blend_name = str(blend_mode).upper() if blend_mode is not None else ""

            is_translucent_like = (
                ("TRANSLUCENT" in blend_name)
                or ("ADDITIVE" in blend_name)
                or ("MODULATE" in blend_name)
            )

            if is_translucent_like and allow_cd is False:
                try:
                    risky.append(str(material.get_name()))
                except Exception:
                    risky.append("<unknown_material>")

        if not risky:
            return ""

        return "疑似半透明材质未开启 Allow Custom Depth Writes: {}".format(", ".join(risky))

    def apply_render_switches(self, component, row):
        """Apply the two render switches and stencil policy to one component."""
        if row.invalid_render_switches:
            return (
                "invalid_render_switches",
                "render_main_pass='{}', render_custom_depth='{}'".format(
                    row.render_main_pass_raw,
                    row.render_custom_depth_raw,
                ),
                "",
            )

        warn_msg = ""

        # RGB 主通道控制。
        # 有些组件可能不支持 render_in_main_pass，所以先判断。
        if not row.dry_run and self.supports_main_pass(component):
            self.set_component_main_pass(component, row.render_main_pass)

        # 进入 MASK：打开 render_custom_depth，并写入 stencil。
        if row.render_custom_depth:
            if row.stencil is None:
                return "missing_stencil", "render_custom_depth=true but stencil missing", ""

            if not row.dry_run:
                self.set_component_stencil(component, row.stencil, enable=True)

            risk_detail = self.inspect_translucent_material_risk(component)
            if risk_detail:
                warn_msg = risk_detail

            return (
                "render_combo_on_on" if row.render_main_pass else "render_combo_off_on",
                "render_in_main_pass={}, render_custom_depth=true, stencil={}".format(
                    str(bool(row.render_main_pass)).lower(),
                    row.stencil,
                ),
                warn_msg,
            )

        # 不进入 MASK：关闭 render_custom_depth，并清空 stencil。
        if not row.dry_run:
            self.clear_component_stencil(component)

        return (
            "render_combo_on_off" if row.render_main_pass else "render_combo_off_off",
            "render_in_main_pass={}, render_custom_depth=false, stencil=0".format(
                str(bool(row.render_main_pass)).lower(),
            ),
            warn_msg,
        )

    def detect_ignore_stencil(self, class_table_csv):
        """Read ignore stencil value from class table, defaulting to 254."""
        ignore_stencil = 254

        with open(class_table_csv, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if str(row.get("semantic_class", "")).strip().lower() == "ignore":
                    ignore_stencil = parse_int(row.get("stencil"), 254)
                    break

        return ignore_stencil

    def apply_writeback(self, rules, sem_cfg, class_table_csv, scene_catalog, component_index, dry_run=False):
        """Apply all rules and return detailed operation logs."""
        ignore_stencil = self.detect_ignore_stencil(class_table_csv)
        rule_builder = SemanticRuleBuilder(sem_cfg, ignore_stencil)

        logs = []
        log("Writeback start. dry_run={}".format(dry_run))
        log("ignore_stencil={}".format(ignore_stencil))

        tx = unreal.ScopedEditorTransaction("Argus Writeback Semantic Stencil")
        _ = tx

        for i, rule in enumerate(rules, start=1):
            row = rule_builder.build_context(rule)
            row.dry_run = bool(dry_run)

            descriptor, resolve_status = scene_catalog.resolve_component_descriptor(component_index, rule)
            component = descriptor.get("component_ref") if descriptor else None

            status = ""
            detail = ""
            warn_msg = ""

            prefix = "[{}/{}] {} / {}".format(
                i,
                len(rules),
                row.actor_name,
                row.component_name,
            )

            if not component:
                status = resolve_status
                detail = "{} / {}".format(row.actor_name, row.component_name)

                if resolve_status == "component_filter_mismatch":
                    filters = []

                    if row.mesh_name:
                        filters.append("mesh_name={}".format(row.mesh_name))
                    if row.mesh_path:
                        filters.append("mesh_path={}".format(row.mesh_path))
                    if row.material_name:
                        filters.append("material_name={}".format(row.material_name))
                    if row.material_path:
                        filters.append("material_path={}".format(row.material_path))
                    if row.material_slot:
                        filters.append("material_slot={}".format(row.material_slot))
                    if row.instance_index is not None:
                        filters.append("instance_index={}".format(row.instance_index))

                    detail = "{} / {}; filters={}".format(
                        row.actor_name,
                        row.component_name,
                        "; ".join(filters),
                    )

                warn("{} -> {}".format(prefix, detail))

            elif not self.supports_stencil(component):
                status = "component_unsupported"

                try:
                    detail = component.get_name()
                except Exception:
                    detail = "<unknown_component>"

                warn("{} -> {} (不支持 CustomDepth/Stencil)".format(prefix, detail))

            else:
                if row.invalid_render_switches:
                    warn(
                        "{} -> 渲染开关非法或缺失: render_main_pass='{}', render_custom_depth='{}'".format(
                            prefix,
                            row.render_main_pass_raw,
                            row.render_custom_depth_raw,
                        )
                    )

                if row.render_custom_depth and row.stencil is None:
                    warn(
                        "{} -> 缺少 stencil，已回退 unknown_stencil={}".format(
                            prefix,
                            parse_int(sem_cfg.get("unknown_stencil"), default=250),
                        )
                    )

                status, detail, warn_msg = self.apply_render_switches(component, row)

                if dry_run:
                    status = "dry_run_{}".format(status)

                log(
                    "{} -> render_main_pass={}, render_custom_depth={}, semantic_class={}, status={}, detail={}".format(
                        prefix,
                        row.render_main_pass,
                        row.render_custom_depth,
                        row.semantic_class,
                        status,
                        detail,
                    )
                )

                if warn_msg:
                    warn("{} -> {}".format(prefix, warn_msg))

            logs.append(
                {
                    "actor_name": row.actor_name,
                    "component_name": row.component_name,
                    "semantic_class": row.semantic_class,
                    "render_main_pass": row.render_main_pass,
                    "render_custom_depth": row.render_custom_depth,
                    "mesh_name": row.mesh_name,
                    "mesh_path": row.mesh_path,
                    "material_name": row.material_name,
                    "material_path": row.material_path,
                    "material_slot": row.material_slot,
                    "instance_index": "" if row.instance_index is None else int(row.instance_index),
                    "stencil": "" if row.stencil is None else int(row.stencil),
                    "status": status,
                    "detail": detail,
                }
            )

        return logs