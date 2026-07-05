# Argus

Argus 是一个可配置的 Unreal Engine 语义数据采集管线，默认同步导出 RGB 图像与语义分割掩码，并支持扩展到多流采集。

它的核心思想是：

- RGB 是否可见，由主渲染通道控制；
- MASK 是否标注，由 CustomDepth / CustomStencil 控制；
- 语义颜色由后处理材质根据 CustomStencil 自动映射；
- 采集结果由 SceneCapture2D 多流机制同步导出。

---

## 目录结构

```text
Argus/
├─ scripts/
│  ├─ common.py
│  ├─ argus_components.py
│  ├─ export_scene_inventory.py
│  ├─ validate_semantic_map.py
│  ├─ writeback_semantic_stencil.py
│  ├─ build_semantic_pp_material.py
│  ├─ setup_dual_capture.py
│  ├─ capture_rgb_and_mask.py
│  └─ batch_capture.py
│
├─ config/
│  ├─ pipeline_config.json
│  ├─ semantic_classes.csv
│  ├─ semantic_map_template.csv
│  └─ camera_poses.csv
│
├─ output/
│  ├─ scene_inventory.json
│  ├─ scene_inventory.csv
│  ├─ semantic_map.csv
│  ├─ semantic_map_validation.csv
│  ├─ stencil_writeback_log.csv
│  ├─ capture_metadata.csv
│  └─ captures/
│
└─ docs/
```

说明：

- `scripts/`：UE Python 脚本与组件代码；
- `config/`：管线配置、语义类别表、相机位姿；
- `output/`：清单、日志、采集元数据、采集结果；
- `docs/`：工作流说明与补充文档。

---

## 核心特性

- 基于 `config/pipeline_config.json` 管理路径、资源名、采集流、相机参数；
- 自动扫描当前 UE 关卡，导出 `scene_inventory.csv`；
- 支持人工 / LLM 基于清单生成 `semantic_map.csv`；
- 支持语义映射质检，在正式回写前发现组件丢失、规则冲突、开关非法等问题；
- 支持将语义规则回写到 UE 组件：
  - `render_in_main_pass`
  - `render_custom_depth`
  - `custom_depth_stencil_value`
- 根据 `semantic_classes.csv` 自动生成语义后处理材质；
- 默认输出彩色语义图：`semantics.mask_encoding = class_color`；
- 也支持灰度 ID 图：`semantics.mask_encoding = stencil_gray`；
- 使用 SceneCapture2D 多流机制进行同步采集；
- 默认双流：`rgb` + `mask`；
- 可扩展到更多流，例如 `depth`、`normal`、`debug` 或其他自定义流；
- 支持相机位姿 CSV 批量采集；
- 支持摄像头内参配置：
  - `fov_deg`
  - `fx_px`
  - `fy_px`
  - `cx_px`
  - `cy_px`
  - `projection_type`
  - `ortho_width`
- 支持断点续采；
- 支持检查并清理不完整 metadata 记录；
- 支持半透明材质进入语义 mask；
- 支持 `ignore` 类和 `unknown` 类约定。

---

## 核心机制

Argus 的语义 mask 不依赖材质颜色，也不依赖 alpha。

它依赖的是：

```text
组件 Render CustomDepth Pass
        +
组件 Custom Depth Stencil Value
        +
语义后处理材质读取 CustomStencil
        +
stencil -> color 映射表
```

也就是说：

```text
RGB 图像由主渲染通道决定
MASK 图像由 CustomDepth / CustomStencil 决定
```

---

## 两通道开关

`semantic_map.csv` 中每条规则使用两个开关控制对象行为。

### `render_main_pass`

控制物体是否出现在 RGB 图像中。

```text
true  -> RGB 可见
false -> RGB 不可见
```

对应 UE 组件属性：

```text
render_in_main_pass
```

### `render_custom_depth`

控制物体是否进入 MASK。

```text
true  -> 写入 CustomDepth / CustomStencil，进入 mask
false -> 不写入 CustomDepth / CustomStencil，不进入 mask
```

对应 UE 组件属性：

```text
render_custom_depth
custom_depth_stencil_value
```

---

## 四种显示 / 标注状态

| render_main_pass | render_custom_depth | RGB | MASK | 用途 |
|---|---:|---:|---:|---|
| true | true | 可见 | 标注 | 正常语义目标 |
| true | false | 可见 | 不标注 | 落叶、碎石、无关杂物 |
| false | true | 不可见 | 标注 | mask 专用代理体，通常不需要 |
| false | false | 不可见 | 不标注 | 完全隐藏或禁用对象 |

当前版本推荐优先使用：

```text
true + true
true + false
false + false
```

`false + true` 只有在你需要专门的 mask 代理体时才使用。

---

## 半透明材质说明

水体、玻璃、透明塑料等半透明材质，如果需要进入 mask，必须同时满足：

```text
组件：
    Render CustomDepth Pass = true
    Custom Depth Stencil Value = 对应语义值

材质：
    Allow Custom Depth Writes = true
```

如果只给组件写了 stencil，但材质没有开启 `Allow Custom Depth Writes`，可能会出现：

```text
RGB 里能看到水 / 玻璃
MASK 里却显示它后面的物体
```

这不是采集脚本错误，而是半透明材质没有写入 CustomDepth。

当前版本不再使用“采集前临时替换材质”的方案。正确方案是在材质层面开启：

```text
Allow Custom Depth Writes
```

`validate_semantic_map.py` 会尝试检查半透明材质风险，并在校验 CSV 中给出 warning。

---

## 语义类别约定

### ignore

`ignore` 类用于表示下游训练或评估时应该忽略的区域。

约定：

```text
semantic_class = ignore
stencil = 254
```

当 `semantic_class=ignore` 且 `render_custom_depth=true` 时，回写逻辑会强制使用 `ignore_stencil`，默认是 `254`。

### unknown

当某个组件需要进入 mask：

```text
render_custom_depth = true
```

但 `semantic_map.csv` 中没有填写 `stencil` 时，会回退到：

```text
unknown_stencil
```

默认建议：

```text
unknown_stencil = 250
```

### background

通常 stencil 为 `0` 的区域被视为背景或未标注区域。

---

## alpha 说明

Argus 当前版本中：

```text
alpha 不作为语义标签依据
```

语义标签只由 RGB 颜色或 stencil 编码决定。

相关配置：

```json
"output": {
  "force_png_opaque": true,
  "force_mask_png_opaque": true
}
```

这两个配置只影响导出的 PNG 文件是否强制 alpha 为 255。它们只影响图片查看体验，不改变语义类别。

---

## 配置文件

### `config/pipeline_config.json`

核心字段示例：

```json
{
  "assets": {
    "root": "/Game/Tools/Semantic",
    "material_name": "M_PP_SemanticMask_Auto",
    "rt_rgb_name": "RT_RGB",
    "rt_mask_name": "RT_MASK"
  },

  "semantics": {
    "class_table_csv": "config/semantic_classes.csv",
    "semantic_map_csv": "output/semantic_map.csv",
    "mask_encoding": "class_color",
    "unknown_stencil": 250
  },

  "render_target": {
    "width": 1920,
    "height": 1080
  },

  "capture": {
    "primary_stream": "rgb",

    "rgb_actor_label": "SC_RGB",
    "mask_actor_label": "SC_MASK",

    "capture_source": "SCS_FINAL_COLOR_LDR",

    "sync_mask_to_rgb": true,

    "capture_every_frame": false,
    "capture_on_movement": false,
    "always_persist_rendering_state": false,

    "camera_intrinsics": {
      "fov_deg": 90,
      "fx_px": null,
      "fy_px": null,
      "cx_px": null,
      "cy_px": null,
      "projection_type": "perspective",
      "ortho_width": null
    }
  },

  "batch": {
    "poses_csv": "config/camera_poses.csv",
    "sleep_seconds": 0.0,
    "continue_on_error": true,
    "skip_existing_capture_id": true,
    "skip_requires_all_files": true,
    "clean_incomplete_metadata": true
  },

  "output": {
    "inventory_json": "output/scene_inventory.json",
    "inventory_csv": "output/scene_inventory.csv",
    "semantic_validation_csv": "output/semantic_map_validation.csv",
    "stencil_writeback_log": "output/stencil_writeback_log.csv",
    "metadata_csv": "output/capture_metadata.csv",
    "capture_dir": "output/captures",
    "file_prefix": "cap",
    "force_png_opaque": true,
    "force_mask_png_opaque": true
  }
}
```

---

## 多流采集配置

如果不配置 `capture.streams`，Argus 会使用默认双流：

```text
rgb
mask
```

也就是兼容旧版配置：

```json
"capture": {
  "rgb_actor_label": "SC_RGB",
  "mask_actor_label": "SC_MASK",
  "sync_mask_to_rgb": true
}
```

如果需要自定义多流，可以添加：

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
      "post_process_material_name": "",
      "sync_to_primary": false,
      "force_png_opaque": true,
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

字段说明：

| 字段 | 说明 |
|---|---|
| `name` | stream 名称，例如 `rgb` / `mask` |
| `actor_label` | 对应 SceneCapture2D Actor Label |
| `rt_asset_name` | RenderTarget 资产名 |
| `file_suffix` | 导出文件后缀 |
| `apply_post_process` | 是否使用后处理材质 |
| `post_process_material_name` | 后处理材质名 |
| `sync_to_primary` | 是否同步到主流位姿与内参 |
| `force_png_opaque` | 是否导出后强制 PNG alpha 为 255 |
| `capture_source` | SceneCaptureSource 枚举名称 |

---

## `semantic_classes.csv`

用于定义语义类别、stencil 和颜色。

示例：

```csv
semantic_class,stencil,color_r,color_g,color_b
background,0,0,0,0
water,1,0,80,255
road,2,80,80,80
grass,3,0,180,0
tree,4,0,100,0
building,5,160,160,160
vehicle,6,255,0,0
sky,7,80,180,255
prop,8,255,180,0
fx,9,255,0,255
unknown,250,255,255,255
ignore,254,0,0,0
```

说明：

- `class_color` 模式下，mask 输出对应 RGB 颜色；
- `stencil_gray` 模式下，mask 输出 `stencil / 255` 灰度；
- 同一个 stencil 如果重复出现，后处理材质只使用第一次映射。

---

## `semantic_map.csv`

`semantic_map.csv` 是语义清洗后的回写规则表。

关键字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `actor_name` | 是 | Actor Label |
| `component_name` | 是 | Component 名称 |
| `semantic_class` | 是 | 语义类别 |
| `render_main_pass` | 是 | 是否在 RGB 中可见 |
| `render_custom_depth` | 是 | 是否进入 MASK |
| `stencil` | 条件必填 | 进入 MASK 时生效，缺失则回退 unknown |
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

示例：

```csv
actor_name,component_name,mesh_name,mesh_path,material_name,material_path,material_slot,instance_index,semantic_class,render_main_pass,render_custom_depth,stencil
Road_01,StaticMeshComponent,,,,,,road,true,true,2
LeafTrash_03,StaticMeshComponent,,,,,,ignore,true,false,
WaterPlane,StaticMeshComponent,,,,,,water,true,true,1
DebugProxy,StaticMeshComponent,,,,,,water,false,true,1
```

---

## `camera_poses.csv`

用于批量采集。

基础字段：

| 字段 | 说明 |
|---|---|
| `id` | 采集 ID / 文件编号 |
| `x,y,z` | 相机位置 |
| `pitch,yaw,roll` | 相机旋转 |
| `fov` | 可选，逐帧 FOV |
| `fx_px,fy_px` | 可选，逐帧焦距 |
| `cx_px,cy_px` | 可选，逐帧主点 |
| `sensor_width_mm` | 可选，传感器宽度 |
| `sensor_height_mm` | 可选，传感器高度 |
| `projection_type` | 可选，`perspective` 或 `orthographic` |
| `ortho_width` | 可选，正交投影宽度 |

示例：

```csv
id,x,y,z,pitch,yaw,roll,fov
pose_000001,0,0,300,-20,0,0,90
pose_000002,200,0,300,-20,15,0,90
pose_000003,400,100,350,-10,30,0,90
```

位姿表中的内参字段优先级高于 `capture.camera_intrinsics`。

---

## 运行顺序

推荐完整流程如下。

### 1. 导出场景清单

运行：

```text
scripts/export_scene_inventory.py
```

输出：

```text
output/scene_inventory.json
output/scene_inventory.csv
```

场景清单会包含：

```text
actor_name
component_name
actor_class
component_class
actor_path
component_path
mesh_name
mesh_path
instance_count
material_names
material_details
```

这些字段用于后续人工或 LLM 生成 `semantic_map.csv`。

---

### 2. 构建语义映射表

基于：

```text
output/scene_inventory.csv
```

人工或使用 LLM 清洗生成：

```text
output/semantic_map.csv
```

可参考模板：

```text
config/semantic_map_template.csv
```

建议保留这些辅助字段：

```text
confidence
reason
review_status
notes
```

这些字段不会影响回写，额外字段会被保留用于诊断。

---

### 3. 校验语义映射表

运行：

```text
scripts/validate_semantic_map.py
```

输出：

```text
output/semantic_map_validation.csv
```

检查内容包括：

- 组件是否存在；
- 同名组件是否需要 mesh/material 消歧；
- mesh 过滤是否匹配；
- material 过滤是否匹配；
- instance_index 是否合法；
- `render_main_pass` 是否非法或缺失；
- `render_custom_depth` 是否非法或缺失；
- stencil 是否缺失；
- 是否存在重复规则；
- 组件是否支持 CustomDepth / Stencil；
- 半透明材质是否可能没有开启 `Allow Custom Depth Writes`。

建议只有在校验结果没有 error 后，再执行正式回写。

---

### 4. 回写语义规则

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

建议先 dry run：

```python
writeback(dry_run=True)
```

确认日志无问题后，再正式回写：

```python
writeback(dry_run=False)
```

如果直接执行脚本，默认行为取决于脚本底部：

```python
if __name__ == "__main__":
    writeback(dry_run=False)
```

---

### 5. 构建语义后处理材质与 RenderTarget

运行：

```text
scripts/build_semantic_pp_material.py
```

默认创建或更新：

```text
/Game/Tools/Semantic/M_PP_SemanticMask_Auto
/Game/Tools/Semantic/RT_RGB
/Game/Tools/Semantic/RT_MASK
```

该材质会读取：

```text
SceneTexture: CustomStencil
```

然后根据 `semantic_classes.csv` 输出语义颜色。

---

### 6. 创建或更新 SceneCapture Actor

运行：

```text
scripts/setup_dual_capture.py
```

默认创建或更新：

```text
SC_RGB
SC_MASK
```

如果配置了 `capture.streams`，则按 streams 创建或更新多路 SceneCapture2D。

该步骤会配置：

- Actor Label；
- RenderTarget；
- CaptureSource；
- 后处理材质；
- 主流 / 从流同步；
- 相机内参。

---

### 7. 单帧采集

运行：

```text
scripts/capture_rgb_and_mask.py
```

该脚本会：

1. 执行一次采集；
2. 校验预期输出文件是否真实存在；
3. 追加写入 `capture_metadata.csv`；
4. 返回 `capture_id` 与文件路径。

默认输出示例：

```text
output/captures/cap_20260427_153000_rgb.png
output/captures/cap_20260427_153000_mask.png
```

---

### 8. 批量采集

运行：

```text
scripts/batch_capture.py
```

该脚本会读取：

```text
config/camera_poses.csv
```

然后逐行采集。

当前版本支持断点续采：

```json
"batch": {
  "skip_existing_capture_id": true,
  "skip_requires_all_files": true,
  "clean_incomplete_metadata": true
}
```

行为：

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

## metadata 输出

采集完成后会追加写入：

```text
output/capture_metadata.csv
```

常见字段：

```text
capture_id
timestamp
rgb_file
mask_file
x
y
z
pitch
yaw
roll
projection_type
fov
fx_px
fy_px
cx_px
cy_px
sensor_width_mm
sensor_height_mm
ortho_width
image_width
image_height
primary_stream
files_json
```

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

## 推荐工作流

常规完整流程：

```text
1. export_scene_inventory.py
2. 人工 / LLM 清洗 scene_inventory.csv，生成 semantic_map.csv
3. validate_semantic_map.py
4. writeback_semantic_stencil.py
5. build_semantic_pp_material.py
6. setup_dual_capture.py
7. capture_rgb_and_mask.py
8. batch_capture.py
```

更稳妥的流程：

```text
1. export_scene_inventory.py
2. 生成 semantic_map.csv
3. validate_semantic_map.py
4. writeback_semantic_stencil.py dry_run=True
5. writeback_semantic_stencil.py dry_run=False
6. build_semantic_pp_material.py
7. setup_dual_capture.py
8. 单帧测试 capture_rgb_and_mask.py
9. 确认 RGB / MASK 对齐无误
10. batch_capture.py
```

---

## 常见问题

### 1. RGB 里能看到水，MASK 里看不到水

检查：

```text
组件 Render CustomDepth Pass 是否开启
组件 Custom Depth Stencil Value 是否正确
材质 Allow Custom Depth Writes 是否开启
```

半透明材质必须开启 `Allow Custom Depth Writes`。

---

### 2. stencil 写了，但 mask 仍然是黑的

检查：

```text
render_custom_depth 是否为 true
后处理材质是否挂在 mask stream 上
mask stream 的 post_process_blend_weight 是否为 1
SceneTexture 是否读取 PPI_CUSTOM_STENCIL
```

---

### 3. mask 颜色不对

检查：

```text
semantic_classes.csv 中 stencil 与颜色是否正确
semantics.mask_encoding 是否为 class_color
build_semantic_pp_material.py 是否重新运行
```

---

### 4. 采集结果 RGB / MASK 不对齐

检查：

```text
mask stream 是否 sync_to_primary=true
setup_dual_capture.py 是否重新运行
batch_capture.py 是否使用同一个 primary_stream
```

---

### 5. 批量采集中断后如何继续？

保持配置：

```json
"skip_existing_capture_id": true,
"skip_requires_all_files": true,
"clean_incomplete_metadata": true
```

重新运行：

```text
scripts/batch_capture.py
```

脚本会跳过已完整完成的 capture_id，并重采不完整记录。

---

### 6. Excel 打开 CSV 中文乱码

所有 CSV 默认使用：

```text
utf-8-sig
```

如果仍然乱码，建议使用 VS Code 或明确以 UTF-8 打开。

---

## UE 版本差异说明

不同 UE 版本中，部分枚举或属性名可能略有差异。

当前脚本已对以下内容做了兼容：

- `SceneCaptureSource` 枚举 fallback；
- `TextureRenderTargetFormat` RGBA8 枚举 fallback；
- `CameraProjectionMode` 枚举别名；
- Actor attach 失败时不终止流程；
- 某些相机属性写入失败时跳过。

如果某个 UE 版本中仍然有 API 差异，优先在下面文件中做兼容：

```text
scripts/common.py
scripts/argus_components.py
```

不建议在入口脚本中堆特殊逻辑。

---

## 设计原则

Argus 当前版本遵循以下分层：

```text
入口脚本：
    只负责读取配置、调用服务、打印日志

组件层：
    负责具体逻辑，如场景扫描、材质生成、语义回写、采集执行

common.py：
    负责路径、配置、解析、UE 通用访问

配置文件：
    控制路径、采集流、相机参数、语义模式

CSV：
    承载可人工检查、可 LLM 清洗的数据
```

这样做的好处是：

- 容易调试；
- 容易替换单个模块；
- 不会把 UE 操作、数据 IO、语义规则混在一起；
- 后续可以继续扩展多流采集、深度图、法线图、实例级标注等能力。

---

## 建议统一的脚本命名

建议项目中统一使用以下脚本名：

```text
export_scene_inventory.py
validate_semantic_map.py
writeback_semantic_stencil.py
build_semantic_pp_material.py
setup_dual_capture.py
capture_rgb_and_mask.py
batch_capture.py
```

避免同时存在多套历史命名，例如：

```text
capture_dual_once.py
capture_batch_from_poses.py
capture_rgb_and_mask.py
batch_capture.py
```

脚本名统一后，README、配置、命令记录和日志会更容易维护。
