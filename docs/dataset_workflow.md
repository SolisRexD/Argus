# 批量自动标注数据集

Argus 的目标是批量获得多样环境、多视角的可追溯标注数据。当前版本把 PCG Lab 扩展为静态场景数据集流水线：离线编译计划 → UE 编辑器建图/生成/采集 → 离线验收与整数标签导出。

这是可扩展的首批环境配方，不代表已经具备海量真实场景或天气素材。当前支持 `urban`（街区）、`park`（绿地与低层建筑）、`yard`（仓储场地）。建筑位置、楼层或尺寸、道具密度和太阳角度随配方/种子变化；道路与地块仍是受控模板。美术来自现有 CitySample 与基础几何体。

## 人工操作

### 1. 在系统 Python 中创建计划

在仓库根目录的终端执行。Pillow/numpy 只装在系统 Python，UE Python 无需安装：

```powershell
python -m pip install -r requirements-dataset.txt
python scripts/plan_dataset.py --spec config/dataset_pcg.json --output output/datasets/my_dataset
```

默认计划：3 种环境 × 2 个布局种子 × 1 个 PCG 种子 × 6 个视角 = 36 对 1920×1080 图像。输出目录必须不存在，脚本拒绝覆盖旧计划。此阶段不连接 UE，也不建地图。

`config/dataset_pcg.json` 控制：

| 字段 | 含义 |
| --- | --- |
| `environments` | `urban`、`park`、`yard` 的子集 |
| `layout_seeds` | 建筑/地块构造、光照种子 |
| `pcg_seeds` | 同一底图内 PCG 道具的位置/旋转变化 |
| `camera_seed` | 独立相机采样种子 |
| `views_per_scene` | 每场景数量，至少 3；交替地面、斜俯视、垂直俯视 |
| `resolution` | 宽高，默认 1920×1080 |
| `warmup_seconds` | 每帧等待编辑器继续 tick 的时长，不等于所有渲染资源已收敛 |
| `split_seed`、`split_ratios` | train/val/test 的哈希分组划分，比例为期望值，小样本可能缺少某一分组 |

同一环境和布局的所有 PCG 种子、视角进入同一 split。相同模板仍可能有明显相似性；跨模板泛化评估应另外保留整个环境族。相机在同一布局的不同 PCG 种子间复用，方便对比遮挡与实例变化。

### 2. 在 UE Python 控制台运行采集

先保存当前工作、停止 PIE。当前实现需要 UE 5.8、PCG/Python/Editor Scripting 插件，以及配方中的素材：

```python
import sys
ROOT = "D:/Study/Code/Python/UE/cv/Argus"
for path in (ROOT, ROOT + "/scripts"):
    if path not in sys.path:
        sys.path.insert(0, path)
import capture_dataset
batch = capture_dataset.run(ROOT + "/output/datasets/my_dataset/plan.json")
```

返回控制权；稍后在**另一条命令**查询：

```python
print(batch.done, batch.error, batch.phase, batch.success_count, batch.skipped_count)
```

批量入口自动创建每个布局独立的 map/graph，生成真实 PCG 实例，核验语义与生成 token，逐帧检查相机净空后采集。每个布局的 map/graph 会在多个 PCG 种子之间复用。它只自动保存本计划生成的地图；不会清除已有地图或覆盖冲突资产。发生错误即停止推进并保留当前现场。

`done=True, error=None, phase='captured'` 表示完成采集，**尚未完成数据集验收**。不要在运行中切图、按 Play、手动修改灯光/材质、另启采集，或热重载 Python 模块。不要在 UE Python 中循环或 sleep 等待。

### 3. 在系统 Python 中验收

```powershell
python scripts/validate_dataset.py output/datasets/my_dataset/plan.json
```

退出码 0 要求所有计划帧通过检查；失败返回 1，并写明失败的场景或帧。检查内容：

- 文件内容 SHA256、可解码 PNG、图像尺寸、不透明 alpha。
- MASK 三通道必须相等；像素值必须来自该场景实际生成的类别 ID（以及背景 0）。
- metadata 位姿/FOV 与计划一致；生成 token、场景/配方/图资产/类别表指纹对应。
- 非背景占比至少 5%、至少两个可见非背景类别、RGB 不是近乎常量；另外记录亮暗区域比例。

通过的 MASK 直接取 R 通道，输出标准 **8 位单通道 `L` PNG**；值就是 `semantic_classes.csv` 的 `stencil`，没有颜色反查或近似量化。原始 MASK 保留，便于审计。颜色重复只影响可视化，不能作为训练标签解码依据。

仓储配方另外提供几何投影回归检查：对垂直俯视相机，按已知建筑尺寸独立计算每个像素中心的射线与方盒交点，比较 building 类轮廓。该检查只覆盖这一受控几何子集：

```powershell
python tools/validate_dataset_projection.py output/datasets/my_dataset/plan.json --output output/datasets/my_dataset/quality/projection.json
```

通过标准为存在可测帧、每帧 IoU ≥ 0.99 且无超出一像素轮廓邻域的差异。它不衡量车辆、植被、透明材质或 RGB 抗锯齿边缘。

训练数据只从 `quality/accepted.jsonl` 读取。该索引包含相对图片路径、标签路径、文件哈希、scene group/split、布局/PCG 种子、相机层次、内外参、生成身份和质量指标。`quality/frames.jsonl` 包含被拒绝的帧；`quality/report.json` 给出总结果。失败和旧 attempt 文件保留；不要按目录 glob 把所有 PNG 都送去训练。

### 4. 续跑

确认无活动任务、没有未保存地图后，重复同一 `capture_dataset.run(plan_path)`。它检查计划、类别表、生成配置与源 Python 文件的指纹；已完成场景的所有产物哈希相符才跳过。缺失或损坏会触发该场景的新 attempt，保留旧文件。

续跑粒度为**整个场景的多视角采集**。当前不在一个失败场景中逐帧拼接，避免不同生成轮次混用。代码/配置改动时创建新计划；不要修改旧计划的 hash 或 source manifest 来绕过检查。验收脚本允许在代码更新后审查历史数据集。

中断后若当前地图是 dirty，先确认没有底层 PCG/capture 工作。未保存的生成结果可以在确认属于本计划后重新载入该布局的已保存地图；手工保存修改可能使 checkpoint 的资产 hash 不符。资产不匹配时保留旧成果、创建新计划，不能盲目清理资产。

## 入口与分层

| 文件/入口 | 环境 | 职责 |
| --- | --- | --- |
| `scripts/plan_dataset.py` | 系统 Python | 校验规格、冻结计划/源代码指纹/配置/类别表/配方/位姿 |
| `scripts/capture_dataset.py:run` | UE 编辑器 | 串行跨布局、创建/核验资产、调用 PCG 采集、checkpoint |
| `scripts/validate_dataset.py` | 系统 Python | 内容与图像验收、单通道标签、训练索引 |
| `argus_core/dataset.py` | 纯 Python | 配方编译、相机采样、分组与标定约定 |
| `argus_backends/ue/dataset_scene.py` | UE | 场景构造、显式标签、相机包围盒净空检查 |
| `capture_pcg_scene.run(..., mode='editor')` | UE | 单场景生成 → 核验 → 多视角采集；原默认仍是 PIE |

需要逐步诊断单个布局时，使用计划内 `configs/<scene_key>.json` 和 `recipes/<group>.json`；构造器为 `DatasetSceneBuilder(plan['classes'], scene['recipe']['environment'])`，通过 `build_pcg_scene.create(config_path, builder=builder)` 创建新地图。已有地图直接打开，随后 `capture_pcg_scene.run(seed, config_path, poses=scene['poses'], mode='editor', validate_pose=validate_camera)`。手工创建的地图不会自动获得批量 checkpoint；不要混用同一计划目录进行手动建图与批量续跑。

## Agent 与 UE 机制

1. 先确认 MCP 真正连通、World/PIE/dirty 状态；Slate 的 `Type=true` 不代表脚本成功，检查 `state.json`、日志和真实产物。
2. 建图/材质编译/保存可能在同线程内嵌套 tick。批量 runner 在调用 UE API 前设 `preparing_scene`，避免重入；切图发生在后续 tick，不能在捕获回调中立即卸载 World。
3. 编辑器采集只适用于静态、非分区、已加载场景。配置仍开启 preparation plan 以保留 warmup，但关闭 pause、streaming wait、player movement、console commands；不是运行 gameplay。需要 BeginPlay、WP、Data Layer、运行时生成的场景仍走已验证的 PIE 路径并独立验收，不能硬套编辑器模式。
4. 相机净空按所有静态 Actor 包围盒与每个 PCG 实例局部包围盒核验，与是否启用碰撞无关；凹形/树冠包围盒可能保守拒绝。它不判断道路可达性，也不保证视图信息量。当前拒绝即停，不自动重采样。
5. 生成语义由配方写入 CustomDepth/stencil；关闭启发式覆盖与 WPO。类别名/ID 必须唯一且背景为 0。同类 PCG 实例仍共享类别 stencil，清单中的实例 ID **不是逐像素实例标签**。
6. 内参使用 UE SceneCapture 水平 FOV、方形像素、中心投影。世界单位 cm，UE X前/Y右/Z上；光学相机 X右/Y下/Z前。`camera_to_world` 显式转换这两套轴。K 采用像素边缘坐标，像素中心为 `(u+0.5,v+0.5)`；OpenCV 整数中心约定需将 cx/cy 减 0.5。当前采样 roll=0；没有标定畸变/自定义投影。
7. 全部可见受控 mesh 写标签，程序天空/地块外区域是背景 0。透明材质、CustomDepth 独立遮挡、TAA 和双流先后渲染仍可能造成边缘差异。字节级 ID 正确、图像可解码及清单正确都不能代替逐像素真值 IoU。
8. 失败保留证据、停止新任务。PCG timeout 解除 Python 等待，不会取消 UE 底层工作；当前没有可靠的跨阶段强制中止 API。

## 扩展边界

地图数量为 `环境数 × 布局种子数`，图片数量再乘以 PCG 种子数和视角数。先用几个布局验证标签与视觉质量，再增量扩大；当前实现还没有分布式渲染、资产回收、数百万帧压力测试或逐帧中断恢复。索引/计划/checkpoint 使用 JSON，源美术二进制与全部材质依赖未纳入内容指纹，跨机器复现还需固定 UE 工程和素材版本。导出与 PNG 处理仍可能阻塞某些编辑器 tick。

后续优先补充可量化的像素真值/遮挡测试、更多素材与地形/天气配方、覆盖度驱动采样，以及吞吐与存储调度。交互控制不是数据集工作流的前提。

本轮实测见 [数据集验证记录](validation/2026-10-10-dataset.md)。
