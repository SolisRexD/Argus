"""
场景对象索引与规则匹配辅助模块。

本模块负责 Argus 管线中的“场景清单”和“组件解析”部分：

1. 扫描当前 UE 关卡中的 Actor。
2. 枚举每个 Actor 下的 PrimitiveComponent。
3. 提取组件的 mesh、material、instance 等信息。
4. 生成可导出的 scene_inventory 行。
5. 构建 component_index，用于语义回写时快速查找组件。
6. 根据 semantic_map.csv 中的规则，解析到唯一组件。

注意：
- 本模块只负责“找到组件”和“导出组件信息”。
- 不负责写入 stencil。
- 不负责修改 render_main_pass / render_custom_depth。
"""

import json

import unreal

from common import get_all_level_actors


class SceneObjectCatalog:
    """
    场景对象目录。

    用于构建可搜索的场景组件描述符，
    供后续语义验证、语义清洗和语义回写使用。
    """

    def actor_label(self, actor):
        """安全获取 Actor Label。"""
        try:
            return actor.get_actor_label()
        except Exception:
            return ""

    def class_name(self, obj):
        """安全获取 UE 对象的类名。"""
        try:
            return obj.get_class().get_name()
        except Exception:
            return ""

    def obj_path(self, obj):
        """安全获取 UE 对象路径。"""
        try:
            return obj.get_path_name()
        except Exception:
            return ""

    def safe_str(self, value):
        """安全转换为字符串。"""
        if value is None:
            return ""

        try:
            return str(value)
        except Exception:
            return ""

    def get_primitive_components(self, actor):
        """
        获取 Actor 下所有 PrimitiveComponent。

        PrimitiveComponent 是 UE 中可参与渲染、碰撞等场景表现的基础组件类型。
        StaticMeshComponent、SkeletalMeshComponent、InstancedStaticMeshComponent 等都属于这一类。
        """
        try:
            return list(actor.get_components_by_class(unreal.PrimitiveComponent))
        except Exception:
            return []

    def get_materials(self, component):
        """安全获取组件使用的材质列表。"""
        try:
            return [m for m in list(component.get_materials()) if m]
        except Exception:
            return []

    def _try_get_editor_property(self, obj, prop_name, default=None):
        """安全读取 UE 编辑器属性。"""
        try:
            return obj.get_editor_property(prop_name)
        except Exception:
            return default

    def _get_mesh_asset(self, component):
        """
        获取组件绑定的 mesh 资产。

        依次尝试：
        1. static_mesh
        2. skeletal_mesh

        如果都没有，则返回 None。
        """
        static_mesh = self._try_get_editor_property(component, "static_mesh", None)
        if static_mesh:
            return static_mesh

        skeletal_mesh = self._try_get_editor_property(component, "skeletal_mesh", None)
        if skeletal_mesh:
            return skeletal_mesh

        return None

    def _get_material_slot_names(self, component, materials):
        """
        获取组件的材质槽名称。

        如果 UE API 获取失败，则回退为：
        - slot_0
        - slot_1
        - slot_2
        """
        try:
            names = [self.safe_str(n) for n in list(component.get_material_slot_names())]
            if names:
                return names
        except Exception:
            pass

        return ["slot_{}".format(i) for i in range(len(materials))]

    def get_component_instance_count(self, component):
        """
        获取实例化组件的实例数量。

        对 InstancedStaticMeshComponent / HierarchicalInstancedStaticMeshComponent，
        如果存在 get_instance_count，则返回实例数量。

        对普通 StaticMeshComponent，返回 0。
        """
        try:
            if hasattr(component, "get_instance_count"):
                return int(component.get_instance_count())
        except Exception:
            pass

        return 0

    def build_component_descriptor(self, actor, component):
        """
        为一个 PrimitiveComponent 构建标准化描述符。

        descriptor 会用于两件事：
        1. 导出 scene_inventory.csv。
        2. 后续根据 semantic_map.csv 规则反查组件。

        descriptor 中保留 component_ref，
        这样 writeback 阶段可以直接拿到 UE 组件引用。
        """
        actor_name = self.actor_label(actor)

        mesh_asset = self._get_mesh_asset(component)
        mesh_name = self.safe_str(mesh_asset.get_name()) if mesh_asset else ""
        mesh_path = self.obj_path(mesh_asset) if mesh_asset else ""

        materials = self.get_materials(component)
        slot_names = self._get_material_slot_names(component, materials)

        material_entries = []

        for idx, material in enumerate(materials):
            slot_name = slot_names[idx] if idx < len(slot_names) else "slot_{}".format(idx)

            material_entries.append(
                {
                    "slot_index": idx,
                    "slot_name": slot_name,
                    "material_name": self.safe_str(material.get_name()),
                    "material_path": self.obj_path(material),
                    "material_class": self.class_name(material),
                    "is_material_instance": "MaterialInstance" in self.class_name(material),
                }
            )

        return {
            "actor_name": actor_name,
            "component_name": self.safe_str(component.get_name()),
            "actor_class": self.class_name(actor),
            "component_class": self.class_name(component),
            "actor_path": self.obj_path(actor),
            "component_path": self.obj_path(component),
            "mesh_name": mesh_name,
            "mesh_path": mesh_path,
            "instance_count": self.get_component_instance_count(component),
            "material_names": [e["material_name"] for e in material_entries],
            "material_entries": material_entries,
            "component_ref": component,
        }

    def build_component_index(self):
        """
        构建组件索引。

        索引结构：

            {
                (actor_name, component_name): [descriptor1, descriptor2, ...]
            }

        使用列表是因为同一个 actor_name + component_name 理论上可能对应多个候选，
        后续还需要通过 mesh/material/instance 等字段进一步消歧。
        """
        index = {}

        for actor in get_all_level_actors():
            actor_name = self.actor_label(actor)

            if not actor_name:
                continue

            for component in self.get_primitive_components(actor):
                descriptor = self.build_component_descriptor(actor, component)
                key = (descriptor["actor_name"], descriptor["component_name"])
                index.setdefault(key, []).append(descriptor)

        return index

    def _normalize_optional_text(self, value):
        """
        规范化可选文本过滤条件。

        CSV 中空单元格可能是：
        - None
        - ""
        - " "
        """
        return str(value or "").strip()

    def _normalize_optional_int(self, value):
        """
        规范化可选整数过滤条件。

        用于 instance_index。

        如果值为空，则返回 None。
        如果能转换为整数，则返回 int。
        如果不能转换，则返回特殊标记 "invalid"。
        """
        if value is None:
            return None

        text = str(value).strip()

        if text == "":
            return None

        try:
            return int(text)
        except Exception:
            return "invalid"

    def _matches_rule(self, descriptor, rule):
        """
        检查一个 descriptor 是否满足 semantic_map.csv 中的一行规则。

        必选定位字段通常是：
        - actor_name
        - component_name

        可选消歧字段包括：
        - mesh_name
        - mesh_path
        - material_name
        - material_path
        - material_slot
        - instance_index

        只有所有非空过滤条件都满足时，才认为匹配。
        """
        mesh_name = self._normalize_optional_text(rule.get("mesh_name", ""))
        mesh_path = self._normalize_optional_text(rule.get("mesh_path", ""))
        material_name = self._normalize_optional_text(rule.get("material_name", ""))
        material_path = self._normalize_optional_text(rule.get("material_path", ""))
        material_slot = self._normalize_optional_text(rule.get("material_slot", ""))
        instance_index = self._normalize_optional_int(rule.get("instance_index", None))

        if mesh_name and descriptor.get("mesh_name", "") != mesh_name:
            return False

        if mesh_path and descriptor.get("mesh_path", "") != mesh_path:
            return False

        if instance_index == "invalid":
            return False

        if instance_index is not None:
            count = int(descriptor.get("instance_count", 0) or 0)

            if count <= 0 or instance_index < 0 or instance_index >= count:
                return False

        # 如果没有任何材质相关过滤条件，则 mesh / instance 匹配后即可通过。
        if not any([material_name, material_path, material_slot]):
            return True

        for entry in descriptor.get("material_entries", []):
            if material_name and entry.get("material_name", "") != material_name:
                continue

            if material_path and entry.get("material_path", "") != material_path:
                continue

            if material_slot and entry.get("slot_name", "") != material_slot:
                continue

            return True

        return False

    def resolve_component_descriptor(self, component_index, rule):
        """
        根据 semantic_map.csv 的一行规则解析组件 descriptor。

        返回：
        - descriptor 或 None
        - 状态码

        状态码包括：
        - ok
        - component_not_found
        - component_filter_mismatch
        - component_ambiguous
        """
        key = (
            str(rule.get("actor_name", "")).strip(),
            str(rule.get("component_name", "")).strip(),
        )

        candidates = component_index.get(key, [])

        if not candidates:
            return None, "component_not_found"

        matched = [c for c in candidates if self._matches_rule(c, rule)]

        if not matched:
            return None, "component_filter_mismatch"

        if len(matched) > 1:
            return None, "component_ambiguous"

        return matched[0], "ok"

    def build_inventory_rows(self):
        """
        构建 scene_inventory 导出行。

        输出内容包括：
        - actor 信息
        - component 信息
        - mesh 信息
        - material 信息
        - instance_count

        这些行后续会被写入：
        - scene_inventory.json
        - scene_inventory.csv

        之后你可以基于这个 CSV 做 LLM 辅助语义清洗。
        """
        rows = []

        for actor in get_all_level_actors():
            if not actor:
                continue

            actor_name = self.actor_label(actor)
            actor_class = self.class_name(actor)
            components = self.get_primitive_components(actor)

            # 如果 Actor 没有 PrimitiveComponent，也保留一行 Actor 级记录。
            if not components:
                rows.append(
                    {
                        "actor_name": actor_name,
                        "component_name": "",
                        "actor_class": actor_class,
                        "component_class": "",
                        "actor_path": self.obj_path(actor),
                        "component_path": "",
                        "mesh_name": "",
                        "mesh_path": "",
                        "instance_count": 0,
                        "material_names": "[]",
                        "material_details": "[]",
                    }
                )
                continue

            for component in components:
                desc = self.build_component_descriptor(actor, component)

                rows.append(
                    {
                        "actor_name": desc["actor_name"],
                        "component_name": desc["component_name"],
                        "actor_class": desc["actor_class"],
                        "component_class": desc["component_class"],
                        "actor_path": desc["actor_path"],
                        "component_path": desc["component_path"],
                        "mesh_name": desc["mesh_name"],
                        "mesh_path": desc["mesh_path"],
                        "instance_count": desc["instance_count"],
                        "material_names": json.dumps(desc["material_names"], ensure_ascii=False),
                        "material_details": json.dumps(desc["material_entries"], ensure_ascii=False),
                    }
                )

        return rows