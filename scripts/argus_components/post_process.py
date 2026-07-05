"""
语义分割后处理材质与 RenderTarget 自动生成服务。

本模块负责 Argus 管线中的 mask 材质和输出目标创建：

1. 读取 semantic_classes.csv。
2. 根据 stencil -> RGB 颜色表生成 Custom Node HLSL。
3. 创建 Post Process 材质。
4. 在材质中读取 SceneTexture: CustomStencil。
5. 将 stencil 映射成语义颜色，并输出到 Emissive Color。
6. 创建或更新 RGB / MASK RenderTarget。

注意：
- 本模块不负责给场景组件写入 Custom Stencil。
- 组件的 stencil 写入由语义回写模块负责。
- 半透明材质能否进入 mask，取决于组件是否启用 Render CustomDepth Pass，
  以及材质是否启用 Allow Custom Depth Writes。
"""

import unreal

from common import log, normalize_color_255_to_1, read_semantic_classes, resolve_path


class SemanticPostProcessBuilder:
    """
    语义后处理材质构建器。

    负责：
    - 创建 / 更新 Post Process 材质。
    - 生成 stencil 到颜色的 HLSL 映射逻辑。
    - 创建 / 更新 RGB 与 MASK RenderTarget。
    """

    def ensure_folder(self, path):
        """
        确保 UE 内容目录存在。

        例如：
            /Game/Argus/Materials
        """
        if not unreal.EditorAssetLibrary.does_directory_exist(path):
            unreal.EditorAssetLibrary.make_directory(path)

    def create_material(self, asset_path, asset_name):
        """
        创建一个新的 Material 资产。

        参数：
        - asset_path: UE 内容目录，例如 /Game/Argus
        - asset_name: 资产名，例如 M_PP_SemanticMask_Auto

        返回：
        - unreal.Material
        """
        tools = unreal.AssetToolsHelpers.get_asset_tools()

        material = tools.create_asset(
            asset_name,
            asset_path,
            unreal.Material,
            unreal.MaterialFactoryNew(),
        )

        if not material:
            raise RuntimeError("创建材质失败: {}/{}".format(asset_path, asset_name))

        return material

    def create_render_target(self, asset_path, asset_name):
        """
        创建 TextureRenderTarget2D 资产。

        参数：
        - asset_path: UE 内容目录
        - asset_name: RenderTarget 资产名

        返回：
        - unreal.TextureRenderTarget2D
        """
        tools = unreal.AssetToolsHelpers.get_asset_tools()

        rt = tools.create_asset(
            asset_name,
            asset_path,
            unreal.TextureRenderTarget2D,
            unreal.TextureRenderTargetFactoryNew(),
        )

        if not rt:
            raise RuntimeError("创建 RenderTarget 失败: {}/{}".format(asset_path, asset_name))

        return rt

    def set_render_target_rgba8(self, rt):
        """
        将 RenderTarget 格式设置为 RGBA8。

        不同 UE 版本里的枚举名可能不同，
        所以这里尝试多个候选名称。

        返回：
        - 实际成功使用的枚举名称
        """
        candidates = [
            "RTF_RGBA8",
            "RTF_R8G8B8A8",
            "RTF_RGBA8_SRGB",
        ]

        last_error = None

        for name in candidates:
            try:
                rt.set_editor_property(
                    "render_target_format",
                    getattr(unreal.TextureRenderTargetFormat, name),
                )
                return name
            except Exception as e:
                last_error = e

        raise RuntimeError("设置 RenderTarget RGBA8 格式失败: {}".format(last_error))

    def load_or_create_rt(self, asset_path, asset_name, width, height):
        """
        加载或创建 RenderTarget，并更新尺寸和格式。

        如果 RenderTarget 已存在：
        - 复用并更新尺寸 / 格式。

        如果不存在：
        - 创建新的 TextureRenderTarget2D。
        """
        full = "{}/{}".format(asset_path, asset_name)

        rt = unreal.EditorAssetLibrary.load_asset(full)

        if not rt:
            rt = self.create_render_target(asset_path, asset_name)

        if hasattr(rt, "init_auto_format"):
            rt.init_auto_format(width, height)
        else:
            rt.set_editor_property("size_x", width)
            rt.set_editor_property("size_y", height)

        fmt_name = self.set_render_target_rgba8(rt)

        try:
            rt.set_editor_property(
                "clear_color",
                unreal.LinearColor(0.0, 0.0, 0.0, 1.0),
            )
        except Exception:
            pass

        unreal.EditorAssetLibrary.save_asset(full)
        log("RenderTarget 已创建/更新为 RGBA8: {} ({})".format(full, fmt_name))

        return rt

    def expr(self, material, cls, x, y):
        """
        在材质图中创建一个表达式节点。

        参数：
        - material: 目标材质
        - cls: 节点类型
        - x, y: 节点在材质图中的位置
        """
        return unreal.MaterialEditingLibrary.create_material_expression(
            material,
            cls,
            x,
            y,
        )

    def connect_expr(self, a, out_name, b, in_name):
        """
        连接两个材质表达式节点。

        例如：
            SceneTexture 输出 -> Custom 节点输入
        """
        ok = unreal.MaterialEditingLibrary.connect_material_expressions(
            a,
            out_name,
            b,
            in_name,
        )

        if not ok:
            raise RuntimeError(
                "连接材质节点失败: {} -> {}".format(
                    a.get_name(),
                    b.get_name(),
                )
            )

    def connect_prop(self, node, out_name, prop):
        """
        将节点输出连接到材质属性。

        例如：
            Custom 节点输出 -> MP_EMISSIVE_COLOR
        """
        ok = unreal.MaterialEditingLibrary.connect_material_property(
            node,
            out_name,
            prop,
        )

        if not ok:
            raise RuntimeError("连接材质属性失败: {}".format(prop))

    def set_custom_output_type(self, custom_node, desired):
        """
        设置 Custom 节点输出类型。

        desired 支持：
        - float3
        - float4

        不同 UE 版本里的枚举名可能不同，
        因此这里尝试多个候选名称。
        """
        candidates = {
            "float3": ["CMOT_FLOAT3", "CMOT_FLOAT_3", "FLOAT3"],
            "float4": ["CMOT_FLOAT4", "CMOT_FLOAT_4", "FLOAT4"],
        }[desired]

        last_error = None

        for item in candidates:
            try:
                custom_node.set_editor_property(
                    "output_type",
                    getattr(unreal.CustomMaterialOutputType, item),
                )
                return
            except Exception as e:
                last_error = e

        raise RuntimeError("设置 Custom 节点输出类型失败 {}: {}".format(desired, last_error))

    def build_hlsl(self, semantic_rows, encoding="class_color"):
        """
        根据语义类别表生成 Custom 节点 HLSL 代码。

        支持两种编码方式：

        1. stencil_gray：
           将 stencil 直接映射为灰度值。
           例如：
               stencil=255 -> 白色
               stencil=0   -> 黑色

        2. class_color：
           根据 semantic_classes.csv 中的 color_r/color_g/color_b
           将 stencil 映射为语义颜色。
        """
        if encoding == "stencil_gray":
            return "\n".join(
                [
                    "float s = floor(Stencil + 0.5);",
                    "float v = saturate(s / 255.0);",
                    "return float3(v, v, v);",
                ]
            )

        # 同一个 stencil 如果出现多次，只保留第一次映射。
        # 这样可以避免类别表中重复 stencil 导致颜色覆盖不确定。
        stencil_to_rgb = {}

        for row in semantic_rows:
            stencil = int(row["stencil"])
            rgb = (
                normalize_color_255_to_1(row["color_r"]),
                normalize_color_255_to_1(row["color_g"]),
                normalize_color_255_to_1(row["color_b"]),
            )

            if stencil not in stencil_to_rgb:
                stencil_to_rgb[stencil] = rgb

        lines = []
        lines.append("int s = (int)(Stencil + 0.5);")
        lines.append("float3 rgb = float3(0.0, 0.0, 0.0);")
        lines.append("switch (s)")
        lines.append("{")

        for stencil in sorted(stencil_to_rgb.keys()):
            r, g, b = stencil_to_rgb[stencil]
            lines.append(
                "    case {}: rgb = float3({:.6f}, {:.6f}, {:.6f}); break;".format(
                    stencil,
                    r,
                    g,
                    b,
                )
            )

        lines.append("    default: break;")
        lines.append("}")
        lines.append("return rgb;")

        return "\n".join(lines)

    def build_material_and_targets(self, cfg):
        """
        根据 pipeline 配置创建 / 更新语义后处理材质和 RenderTarget。

        配置来源：
        - cfg["assets"]
        - cfg["render_target"]
        - cfg["semantics"]

        返回：
        - material_path
        - mask_encoding
        - rt_rgb
        - rt_mask
        """
        assets = cfg["assets"]
        render_cfg = cfg["render_target"]
        sem_cfg = cfg["semantics"]

        mask_encoding = str(sem_cfg.get("mask_encoding", "class_color")).strip().lower()

        if mask_encoding not in {"stencil_gray", "class_color"}:
            raise RuntimeError("非法的 semantics.mask_encoding: {}".format(mask_encoding))

        class_table_csv = resolve_path(sem_cfg["class_table_csv"])
        semantic_rows = read_semantic_classes(class_table_csv)

        asset_root = assets["root"]
        material_name = assets["material_name"]
        rt_rgb_name = assets["rt_rgb_name"]
        rt_mask_name = assets["rt_mask_name"]

        self.ensure_folder(asset_root)

        material_path = "{}/{}".format(asset_root, material_name)

        # 为了保证材质图完全干净，这里如果已有旧材质，就删除后重建。
        if unreal.EditorAssetLibrary.does_asset_exist(material_path):
            unreal.EditorAssetLibrary.delete_asset(material_path)

        mat = self.create_material(asset_root, material_name)

        # 设置为 Post Process 材质。
        mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_POST_PROCESS)

        # 某些 UE 版本中 Material 有 is_blendable 属性。
        if hasattr(mat, "is_blendable"):
            mat.set_editor_property("is_blendable", True)

        unreal.MaterialEditingLibrary.delete_all_material_expressions(mat)

        # 读取 SceneTexture: CustomStencil。
        scene_tex = self.expr(
            mat,
            unreal.MaterialExpressionSceneTexture,
            -700,
            -100,
        )
        scene_tex.set_editor_property(
            "scene_texture_id",
            unreal.SceneTextureId.PPI_CUSTOM_STENCIL,
        )

        # 创建 Custom 节点，把 stencil 映射成颜色。
        custom = self.expr(
            mat,
            unreal.MaterialExpressionCustom,
            -350,
            -100,
        )

        custom_input = unreal.CustomInput()
        custom_input.set_editor_property("input_name", "Stencil")
        custom.set_editor_property("inputs", [custom_input])

        self.set_custom_output_type(custom, "float3")

        custom.set_editor_property(
            "code",
            self.build_hlsl(semantic_rows, encoding=mask_encoding),
        )

        self.connect_expr(scene_tex, "", custom, "Stencil")
        self.connect_prop(custom, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

        unreal.MaterialEditingLibrary.layout_material_expressions(mat)
        unreal.MaterialEditingLibrary.recompile_material(mat)
        unreal.EditorAssetLibrary.save_asset(material_path)

        width = int(render_cfg.get("width", 1920))
        height = int(render_cfg.get("height", 1080))

        rt_rgb = self.load_or_create_rt(asset_root, rt_rgb_name, width, height)
        rt_mask = self.load_or_create_rt(asset_root, rt_mask_name, width, height)

        return {
            "material_path": material_path,
            "mask_encoding": mask_encoding,
            "rt_rgb": rt_rgb.get_path_name(),
            "rt_mask": rt_mask.get_path_name(),
        }