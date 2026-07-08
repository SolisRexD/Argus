# Argus 工作流程

本文档说明 Argus 从场景扫描、语义清洗、规则回写到 RGB/MASK 数据采集的推荐执行流程。

Argus 的核心原则是：

- RGB 是否可见，由主渲染通道控制；
- MASK 是否可见，由 CustomDepth / CustomStencil 控制；
- 语义颜色由后处理材质读取 CustomStencil 后映射得到；
- alpha 不参与语义类别判断。

---

## 1. 运行前需要准备的文件

以下文件应在正式运行前准备好，通常放在 `config/` 目录中。

| 文件 | 作用 | 是否手写 |
|---|---|---:|
| `config/pipeline_config.json` | 主配置文件，控制路径、采集流、RenderTarget、相机内参、批量采集策略 | 是 |
| `config/semantic_classes.csv` | 语义类别表，定义 `semantic_class -> stencil -> color` | 是 |
| `config/semantic_map_template.csv` | 语义映射表模板，供人工或 LLM 清洗时参考 | 是 |
| `config/camera_poses.csv` | 批量采集位姿表 | 是 |

以下文件不是初始模板，而是运行中产生或清洗得到。

| 文件 | 来源 | 说明 |
|---|---|---|
| `output/scene_inventory.csv` | `export_scene_inventory.py` 自动导出 | 当前关卡对象清单 |
| `output/scene_inventory.json` | `export_scene_inventory.py` 自动导出 | 当前关卡对象清单 JSON 版 |
| `output/semantic_map.csv` | 基于 `scene_inventory.csv` 人工或 LLM 清洗得到 | 正式语义回写规则 |
| `output/semantic_map_validation.csv` | `validate_semantic_map.py` 自动生成 | 语义规则质检结果 |
| `output/stencil_writeback_log.csv` | `writeback_semantic_stencil.py` 自动生成 | 语义回写日志 |
| `output/capture_metadata.csv` | 单帧或批量采集自动追加 | 采集元数据 |
| `output/captures/` | 单帧或批量采集自动生成 | 图像输出目录 |

---

## 2. 推荐执行顺序

推荐完整流程如下：

1. 准备 `pipeline_config.json`、`semantic_classes.csv`、`camera_poses.csv`。
2. 运行 `export_scene_inventory.py` 导出当前场景对象清单。
3. 基于 `scene_inventory.csv` 进行人工或 LLM 清洗，生成 `output/semantic_map.csv`。
4. 运行 `validate_semantic_map.py` 进行语义映射质检。
5. 运行 `writeback_semantic_stencil.py` 将渲染开关与 stencil 写回 UE 组件。
6. 运行 `build_semantic_pp_material.py` 创建语义后处理材质与 RenderTarget。
7. 运行 `setup_dual_capture.py` 创建或更新 SceneCapture2D Actor。
8. 先运行 `capture_rgb_and_mask.py` 做单帧测试。
9. 确认 RGB/MASK 对齐且语义正确后，运行 `batch_capture.py` 批量采集。

对应脚本顺序：

```text
scripts/export_scene_inventory.py
scripts/validate_semantic_map.py
scripts/writeback_semantic_stencil.py
scripts/build_semantic_pp_material.py
scripts/setup_dual_capture.py
scripts/capture_rgb_and_mask.py
scripts/batch_capture.py
```

注意：第 2 步和第 3 步之间需要人工或 LLM 介入。`validate_semantic_map.py` 不能在 `semantic_map.csv` 生成前运行。

---

## 3. UE Python 控制台执行命令

在 UE Python 控制台中，可以按顺序执行：

```python
import runpy
runpy.run_path(r"<workspace>/Argus/scripts/export_scene_inventory.py", run_name="__main__")
```

生成并清洗 `output/semantic_map.csv` 后，再继续执行：

```python
import runpy
runpy.run_path(r"<workspace>/Argus/scripts/validate_semantic_map.py", run_name="__main__")
runpy.run_path(r"<workspace>/Argus/scripts/writeback_semantic_stencil.py", run_name="__main__")
runpy.run_path(r"<workspace>/Argus/scripts/build_semantic_pp_material.py", run_name="__main__")
runpy.run_path(r"<workspace>/Argus/scripts/setup_dual_capture.py", run_name="__main__")
runpy.run_path(r"<workspace>/Argus/scripts/capture_rgb_and_mask.py", run_name="__main__")
```

批量采集时执行：

```python
import runpy
runpy.run_path(r"<workspace>/Argus/scripts/batch_capture.py", run_name="__main__")
```

其中 `<workspace>/Argus` 替换成你的实际项目路径。

---

## 4. 代码架构

当前 Argus 采用“入口脚本 + 组件层”的结构。

入口脚本位于 `scripts/`，只负责编排流程：

| 脚本 | 作用 |
|---|---|
| `export_scene_inventory.py` | 导出场景对象清单 |
| `validate_semantic_map.py` | 校验语义映射规则 |
| `writeback_semantic_stencil.py` | 将语义规则写回 UE 组件 |
| `build_semantic_pp_material.py` | 创建语义后处理材质与 RenderTarget |
| `setup_dual_capture.py` | 创建或更新 SceneCapture2D Actor |
| `capture_rgb_and_mask.py` | 单帧采集 |
| `batch_capture.py` | 按位姿 CSV 批量采集 |

组件层位于 `scripts/argus_components/`，按职责拆分：

| 文件 | 职责 |
|---|---|
| `scene_objects.py` | 扫描场景对象，构建组件索引，导出 inventory 行 |
| `annotation_control.py` | 控制 `render_in_main_pass`、`render_custom_depth`、`custom_depth_stencil_value` |
| `capture_system.py` | 管理 SceneCapture2D、RenderTarget、单帧采集、多流同步 |
| `post_process.py` | 创建语义后处理材质，生成 stencil 到颜色的 HLSL 映射 |
| `data_pipeline.py` | 写出 CSV/JSON、metadata、writeback log |

公共工具位于：

```text
scripts/common.py
```

负责：

- 配置读取；
- 路径解析；
- 日志输出；
- UE Actor 查询；
- 资产加载；
- CSV 字段解析。

---

## 5. 场景清单导出

运行：

```text
scripts/export_scene_inventory.py
```

输出：

```text
output/scene_inventory.csv
output/scene_inventory.json
```

`scene_inventory.csv` 会包含：

- `actor_name`
- `component_name`
- `actor_class`
- `component_class`
- `actor_path`
- `component_path`
- `mesh_name`
- `mesh_path`
- `instance_count`
- `material_names`
- `material_details`

其中 `material_details` 是 JSON 字符串，包含材质槽、材质路径、材质类型等信息。

这一步只负责忠实导出当前场景信息，不做语义分类。

---

## 6. 语义映射表清洗

基于：

```text
output/scene_inventory.csv
```

生成：

```text
output/semantic_map.csv
```

建议参考：

```text
config/semantic_map_template.csv
```

`semantic_map.csv` 的关键字段：

| 字段 | 是否必填 | 说明 |
|---|---:|---|
| `actor_name` | 是 | Actor Label |
| `component_name` | 是 | 组件名 |
| `semantic_class` | 是 | 语义类别 |
| `render_main_pass` | 是 | 是否在 RGB 中可见 |
| `render_custom_depth` | 是 | 是否进入 MASK |
| `stencil` | 条件必填 | `render_custom_depth=true` 时生效，缺失则回退 unknown |
| `mesh_name` | 否 | 用于同名组件消歧 |
| `mesh_path` | 否 | 用于同名组件消歧 |
| `material_name` | 否 | 用于材质消歧 |
| `material_path` | 否 | 用于材质消歧 |
| `material_slot` | 否 | 用于材质槽消歧 |
| `instance_index` | 否 | 用于实例化组件定位与校验 |

布尔字段支持：

```text
true / false
1 / 0
yes / no
y / n
on / off
```

建议保留以下辅助字段，方便人工复查或 LLM 清洗：

- `confidence`
- `reason`
- `review_status`
- `notes`

这些额外字段不会影响回写。

---

## 7. 渲染开关规则

Argus 使用两个开关解耦 RGB 与 MASK。

### `render_main_pass`

控制 RGB 主通道是否可见。

对应 UE 属性：

```text
render_in_main_pass
```

### `render_custom_depth`

控制是否进入语义 MASK。

对应 UE 属性：

```text
render_custom_depth
custom_depth_stencil_value
```

四种组合：

| render_main_pass | render_custom_depth | RGB | MASK | 用途 |
|---|---:|---:|---:|---|
| `true` | `true` | 可见 | 可见 | 普通语义目标 |
| `true` | `false` | 可见 | 不可见 | 落叶、碎石、小杂物等只保留 RGB 的对象 |
| `false` | `true` | 不可见 | 可见 | mask 专用代理体，通常不需要 |
| `false` | `false` | 不可见 | 不可见 | 完全隐藏或禁用对象 |

当前版本中，语义是否进入 MASK 只由 CustomDepth / CustomStencil 控制，alpha 不参与语义判断。

---

## 8. ignore 与 unknown 约定

### ignore

`ignore` 类用于下游训练或评估时忽略某些区域。

约定：

```text
semantic_class = ignore
stencil = 254
```

当 `semantic_class=ignore` 且 `render_custom_depth=true` 时，回写逻辑会强制使用 ignore stencil，默认值为 `254`。

如果某个对象只是 RGB 可见、MASK 不标注，推荐使用：

```text
render_main_pass=true
render_custom_depth=false
```

这种情况下 stencil 可以留空。

### unknown

当：

```text
render_custom_depth=true
```

但 `semantic_map.csv` 中没有填写 stencil 时，会回退到：

```text
semantics.unknown_stencil
```

默认建议值为：

```text
250
```

---

## 9. 半透明材质注意事项

如果水体、玻璃、透明塑料等半透明材质需要进入 MASK，必须同时满足：

```text
组件：
    Render CustomDepth Pass = true
    Custom Depth Stencil Value = 对应语义值

材质：
    Allow Custom Depth Writes = true
```

如果材质没有启用 `Allow Custom Depth Writes`，可能出现：

```text
RGB 里能看到半透明物体
MASK 里却显示其背后的物体
```

当前版本不使用“采集前临时替换材质”的方案。正确做法是在材质层面开启 `Allow Custom Depth Writes`。

`validate_semantic_map.py` 会尝试检查半透明材质风险，并在 `semantic_map_validation.csv` 中给出 warning。

---

## 10. 语义映射质检

运行：

```text
scripts/validate_semantic_map.py
```

输出：

```text
output/semantic_map_validation.csv
```

检查内容包括：

- 规则是否能匹配到当前场景组件；
- mesh / material / slot / instance 过滤条件是否匹配；
- 是否命中多个候选组件；
- `render_main_pass` 是否缺失或非法；
- `render_custom_depth` 是否缺失或非法；
- 进入 MASK 时 stencil 是否缺失；
- 是否存在重复规则；
- 组件是否支持 CustomDepth / CustomStencil；
- 半透明材质是否可能未开启 `Allow Custom Depth Writes`。

常见状态：

| 状态 | 含义 |
|---|---|
| `ok` | 规则正常 |
| `component_not_found` | 找不到对应 actor/component |
| `component_filter_mismatch` | actor/component 找到了，但 mesh/material/slot/instance 条件不匹配 |
| `component_ambiguous` | 过滤条件不足，命中多个候选 |
| `invalid_render_switches` | 渲染开关缺失或非法 |
| `missing_stencil` | 进入 MASK 但 stencil 缺失 |
| `duplicate_rule` | 重复规则 |
| `component_unsupported` | 组件不支持 CustomDepth / CustomStencil |
| `translucent_custom_depth_risk` | 半透明材质可能没有写入 CustomDepth |

建议只有在没有 error 后，再执行正式回写。

---

## 11. 语义规则回写

运行：

```text
scripts/writeback_semantic_stencil.py
```

输出：

```text
output/stencil_writeback_log.csv
```

该步骤会写入 UE 组件属性：

```text
render_in_main_pass
render_custom_depth
custom_depth_stencil_value
```

建议先 dry run，再正式回写。

在 UE 控制台中可以执行：

```python
import runpy
runpy.run_path(r"<workspace>/Argus/scripts/writeback_semantic_stencil.py", run_name="__main__")
```

如果脚本底部是：

```python
if __name__ == "__main__":
    writeback(dry_run=False)
```

则直接执行脚本会正式写回。

如果想先演练，可以临时改成：

```python
if __name__ == "__main__":
    writeback(dry_run=True)
```

---

## 12. 构建语义材质与 RenderTarget

运行：

```text
scripts/build_semantic_pp_material.py
```

该步骤会创建或更新：

```text
/Game/Tools/Semantic/M_PP_SemanticMask_Auto
/Game/Tools/Semantic/RT_RGB
/Game/Tools/Semantic/RT_MASK
```

后处理材质会读取：

```text
SceneTexture: CustomStencil
```

然后根据 `semantic_classes.csv` 中的 stencil 和颜色输出语义图。

语义图编码方式由配置控制：

```json
"semantics": {
  "mask_encoding": "class_color"
}
```

可选值：

| 值 | 说明 |
|---|---|
| `class_color` | 彩色语义图 |
| `stencil_gray` | 灰度 ID 图，输出 `stencil / 255` |

---

## 13. 配置 SceneCapture2D

运行：

```text
scripts/setup_dual_capture.py
```

默认创建或更新：

```text
SC_RGB
SC_MASK
```

如果配置了 `capture.streams`，则按 stream 配置创建或更新多路 SceneCapture2D。

每个 stream 可独立配置：

- Actor Label；
- RenderTarget；
- 文件后缀；
- 是否应用后处理材质；
- 是否同步到主流；
- CaptureSource；
- PNG alpha 是否强制改为 255。

主流由：

```json
"capture": {
  "primary_stream": "rgb"
}
```

指定。

---

## 14. 采集流配置

如果不配置 `capture.streams`，Argus 会使用默认双流：

```text
rgb
mask
```

推荐显式配置：

```json
"capture": {
  "primary_stream": "rgb",
  "streams": [
    {
      "name": "rgb",
      "actor_label": "SC_RGB",
      "rt_asset_name": "RT_RGB",
      "file_suffix": "rgb",
      "apply_post_process": false,
      "sync_to_primary": false,
      "force_png_opaque": false,
      "capture_source": "SCS_FINAL_COLOR_LDR"
    },
    {
      "name": "mask",
      "actor_label": "SC_MASK",
      "rt_asset_name": "RT_MASK",
      "file_suffix": "mask",
      "apply_post_process": true,
      "post_process_material_name": "M_PP_SemanticMask_Auto",
      "sync_to_primary": true,
      "force_png_opaque": true,
      "capture_source": "SCS_FINAL_COLOR_LDR"
    }
  ]
}
```

未来如果要增加 `depth`、`normal`、`debug` 等流，优先通过新增 stream 配置扩展，而不是重写入口脚本。

---

## 15. 摄像头内参与位姿

`capture.camera_intrinsics` 用于统一管理 SceneCapture2D 内参。

支持字段：

- `projection_type`
- `fov_deg`
- `fx_px`
- `fy_px`
- `cx_px`
- `cy_px`
- `sensor_width_mm`
- `sensor_height_mm`
- `image_width`
- `image_height`
- `aspect_ratio`
- `use_custom_aspect_ratio`
- `constrain_aspect_ratio`
- `ortho_width`

如果提供了 `fx_px` 和图像宽度，但没有提供 `fov_deg`，代码会自动推导水平 FOV。

`camera_poses.csv` 可逐帧覆盖部分内参：

- `fov`
- `fx_px`
- `fy_px`
- `cx_px`
- `cy_px`
- `sensor_width_mm`
- `sensor_height_mm`
- `projection_type`
- `ortho_width`

位姿 CSV 示例：

```csv
id,x,y,z,pitch,yaw,roll,fov,fx_px,fy_px,cx_px,cy_px,projection_type,ortho_width
pose_000001,0,0,300,-20,0,0,90,,,,,perspective,
pose_000002,200,0,300,-20,15,0,90,,,,,perspective,
pose_000003,400,100,350,-10,30,0,90,,,,,perspective,
```

---

## 16. 单帧采集

运行：

```text
scripts/capture_rgb_and_mask.py
```

该脚本会：

1. 读取配置；
2. 执行一次多流同步采集；
3. 校验预期输出文件是否真实存在；
4. 追加写入 `output/capture_metadata.csv`；
5. 打印本次采集结果。

建议第一次正式批量采集前，先用单帧采集确认：

- RGB 是否正常；
- MASK 是否正常；
- RGB/MASK 是否对齐；
- 半透明物体是否正确进入或不进入 mask；
- ignore / unknown 是否符合预期。

---

## 17. 批量采集

运行：

```text
scripts/batch_capture.py
```

该脚本会读取：

```text
config/camera_poses.csv
```

并逐行采集。

推荐 batch 配置：

```json
"batch": {
  "poses_csv": "config/camera_poses.csv",
  "sleep_seconds": 0.0,
  "continue_on_error": true,
  "skip_existing_capture_id": true,
  "skip_requires_all_files": true,
  "clean_incomplete_metadata": true
}
```

断点续采逻辑：

```text
metadata 中已有 capture_id，且对应输出文件完整存在
    -> 跳过

metadata 中已有 capture_id，但输出文件缺失
    -> 可选清理旧 metadata 记录
    -> 重新采集

本次采集后文件没有真实生成
    -> 报错
    -> 不写 metadata

camera_poses.csv 内部出现重复 id
    -> 后面的重复项跳过
```

---

## 18. metadata 输出

采集元数据写入：

```text
output/capture_metadata.csv
```

常见字段：

- `capture_id`
- `timestamp`
- `rgb_file`
- `mask_file`
- `x`
- `y`
- `z`
- `pitch`
- `yaw`
- `roll`
- `projection_type`
- `fov`
- `fx_px`
- `fy_px`
- `cx_px`
- `cy_px`
- `sensor_width_mm`
- `sensor_height_mm`
- `ortho_width`
- `image_width`
- `image_height`
- `primary_stream`
- `files_json`

其中：

```text
rgb_file
mask_file
```

是旧版兼容字段。

多流采集时，推荐读取：

```text
files_json
```

示例：

```json
{
  "rgb": "D:/Argus/output/captures/pose_000001_rgb.png",
  "mask": "D:/Argus/output/captures/pose_000001_mask.png",
  "depth": "D:/Argus/output/captures/pose_000001_depth.hdr"
}
```

同时也会生成便捷字段：

```text
rgb_file
mask_file
depth_file
normal_file
...
```

---

## 19. 当前默认输出行为

推荐默认：

```json
"semantics": {
  "mask_encoding": "class_color",
  "unknown_stencil": 250
},
"output": {
  "force_png_opaque": false,
  "force_mask_png_opaque": false
}
```

说明：

- `class_color` 输出彩色语义图；
- `stencil_gray` 输出灰度 ID 图；
- `force_png_opaque` 只影响 PNG alpha 显示，不影响语义标签；
- `force_mask_png_opaque` 只影响 PNG alpha 显示，不影响语义标签；
- 语义类别以 RGB 颜色或 stencil 编码为准，不以 alpha 为准。

---

## 20. 常见问题

### RGB 里能看到水，MASK 里看不到水

检查：

```text
组件 Render CustomDepth Pass 是否开启
组件 Custom Depth Stencil Value 是否正确
材质 Allow Custom Depth Writes 是否开启
```

半透明材质必须开启 `Allow Custom Depth Writes`。

### stencil 写了，但 mask 仍然是黑的

检查：

```text
render_custom_depth 是否为 true
后处理材质是否挂在 mask stream 上
mask stream 的 post_process_blend_weight 是否为 1
SceneTexture 是否读取 PPI_CUSTOM_STENCIL
```

### mask 颜色不对

检查：

```text
semantic_classes.csv 中 stencil 与颜色是否正确
semantics.mask_encoding 是否为 class_color
build_semantic_pp_material.py 是否重新运行
```

### RGB / MASK 不对齐

检查：

```text
mask stream 是否 sync_to_primary=true
setup_dual_capture.py 是否重新运行
capture.primary_stream 是否正确
```

### 批量采集中断后如何继续

保持：

```json
"skip_existing_capture_id": true,
"skip_requires_all_files": true,
"clean_incomplete_metadata": true
```

然后重新运行：

```text
scripts/batch_capture.py
```

脚本会跳过已经完整完成的 capture_id，并重采不完整记录。

---

## 21. 推荐工作方式

最稳妥的实际流程：

```text
1. 检查 config/pipeline_config.json
2. 检查 config/semantic_classes.csv
3. 检查 config/camera_poses.csv
4. 运行 export_scene_inventory.py
5. 清洗 scene_inventory.csv，生成 semantic_map.csv
6. 运行 validate_semantic_map.py
7. 修正 semantic_map.csv 中的 error / warning
8. 运行 writeback_semantic_stencil.py dry_run=True
9. 确认 stencil_writeback_log.csv
10. 运行 writeback_semantic_stencil.py dry_run=False
11. 运行 build_semantic_pp_material.py
12. 运行 setup_dual_capture.py
13. 运行 capture_rgb_and_mask.py 做单帧测试
14. 确认 RGB / MASK 输出无误
15. 运行 batch_capture.py 批量采集
```

原则：

- 入口脚本只负责编排；
- 组件层负责具体逻辑；
- CSV 用于人工检查和 LLM 清洗；
- 所有自动生成文件都放在 `output/`；
- 所有运行前模板都放在 `config/`。
