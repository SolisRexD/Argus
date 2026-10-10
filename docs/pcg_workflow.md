# PCG 第一版：生成固定场景，再采集多视角

需要自动切换环境/布局、采样视角、输出整数类别标签与验收清单时，使用 [数据集工作流](dataset_workflow.md)。本页保留单场景 PCG Lab 的 PIE 基线；`capture_pcg_scene.run` 新增显式 `mode="editor"`，仅适用于关闭 gameplay/streaming 操作的静态配置。

当前实现使用 UE 5.8 核心 PCG：CPU、非分区、OnDemand。真正的 PCG Graph 负责创建网格点、随机变换和 Static Mesh Spawner；Python 负责建图、等待、核验和采集调度。无需 PCGPythonInterop。

地图 `/Game/Argus/PCG/Maps/Argus_PCG_Lab` 使用原生 GameModeBase，约 95×95 米。道路、三栋建筑、地面和灯光沿用干净关卡的构造方法；树、车、长椅、垃圾桶由 PCG 生成。CaptureLab 原地图与默认配置保持原用途。

## 人工逐脚本运行

在 UE 编辑器 Python 控制台初始化路径：

```python
import sys
ROOT = "D:/Study/Code/Python/UE/cv/Argus"
for path in (ROOT, ROOT + "/scripts"):
    if path not in sys.path:
        sys.path.insert(0, path)
CFG = ROOT + "/config/pipeline_pcg.json"
```

首次创建，先停止 PIE、保存当前地图：

```python
import build_pcg_scene
build_pcg_scene.create(CFG)
```

脚本拒绝覆盖已有地图或图资产。若已创建，直接打开：

```python
import unreal
level = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
level.load_level("/Game/Argus/PCG/Maps/Argus_PCG_Lab")
```

需要在编辑器预览布局时，运行生成脚本：

```python
import generate_pcg_scene
generation = generate_pcg_scene.generate(seed=42, config_path=CFG)
```

返回控制权，稍后在另一条命令检查。不能在 UE Python 中循环或 sleep 等待：

```python
print(generation.done, generation.error, generation.phase)
if generation.done and generation.error is None:
    print(generation.result["counts"], generation.result["scene_sha256"])
    level.save_current_level()
```

可选，单独导出当前场景清单；目标文件必须不存在：

```python
import export_pcg_manifest
manifest = export_pcg_manifest.export("output/pcg_lab/inspection_seed42.json", CFG)
```

这个导出只做检查，不是新一代生成完成的证明。

正式采集：点击 Play，运行以下入口。它会先清理 PIE 中旧的 PCG 实例，再按 seed 生成，验证后串行采集配置中的全部位姿：

```python
import capture_pcg_scene
run = capture_pcg_scene.run(seed=42, config_path=CFG)
```

稍后查询：

```python
print(run.done, run.error, run.phase, run.success_count, run.failed_count)
print(run.directory)
```

确认 `done=True`、`error=None`、`phase='complete'`，默认三个视角全部成功，并检查实际图片后，才换 seed：

```python
run = capture_pcg_scene.run(seed=43, config_path=CFG)
```

采完停止 PIE。编辑器地图保持之前保存的 seed；PIE 内更换 seed 不会回写地图。不需要 CitySample prepare/restore。

| 入口 | 作用 | 可用阶段 |
| --- | --- | --- |
| `build_pcg_scene.create(CFG)` | 新建地图、PCG 图、明确标注的底图、采集资产 | 编辑器，目标不存在 |
| `generate_pcg_scene.generate(seed, CFG)` | 异步清理、生成、验证，返回 job | 编辑器或 PIE，未暂停 |
| `export_pcg_manifest.export(path, CFG)` | 校验并导出当前实例/底图；拒绝覆盖文件 | 生成完成后 |
| `capture_pcg_scene.run(seed, CFG, poses=None)` | 生成 → 验证 → 固定场景多视角采集 | PIE |

## 配方与产物

`config/pcg_lab_recipe.json` 定义五条分支：树木两排各四棵、车辆四辆、长椅和垃圾桶各三个。每条规则指定 mesh、类别、数量、间距、中心、抖动、旋转范围和固定缩放。随机变换来自 PCG 的 seed，Python 没有替代 PCG 随机摆放实例。

`config/pipeline_pcg.json` 使用独立采集资产，关闭自动语义推断，沿用三个 CaptureLab 相机位姿。生成 descriptor 直接写 CustomDepth/stencil；同一 ISM 只承载一种类别，分支 tags 记录来源。底图也按构造规则显式标注。树木实例关闭 WPO，以减少多视角场景变化。

修改布局配方时，复制配方并使用新的 `map_path`、`graph_path`；复制 pipeline 配置，把 `pcg.recipe` 指向新文件，按需为 `assets.root` 设置独立目录。创建器目前不更新已有图，整份配方（包括默认 seed、timeout）的指纹必须与建图时一致；切 seed 应使用 `generate/run` 参数。此版本没有做任意配方的道路避让、碰撞求解和连通性检查，改变尺寸/抖动后要重新验收。

每次采集写入新的 `output/pcg_lab/runs/<UTC时间>_seed<N>_<随机后缀>/`：

- 配方、pipeline、位姿、类别映射和完整类别 CSV 快照。
- `generation_manifest.json`：generation ID、seed、配方/类别/已保存图资产指纹、UE 版本、18 个真实实例的 mesh/世界变换/类别/来源、157 个底图网格。
- `captures/`、`capture_metadata.csv`、`result.json`：每帧 metadata 包含 `pcg_generation_id`、`pcg_seed`、`pcg_scene_sha256`、`pcg_recipe_sha256`、`pcg_graph_sha256`。

实例内容 ID 来自规则、mesh、类别和变换，组件路径与实例索引只用于定位。它不是跨不同 seed 的对象对应关系。`scene_sha256` 用于比较布局/语义；不代表渲染像素完全相同，也不包含所有源美术资产的二进制内容。图资产 SHA 对应磁盘保存版本；运行前保存图，不在采集时手工修改图。

此入口不续跑旧目录，因此不同 seed 或重拍不会因旧 capture ID 被跳过。中断后保留目录，用新 run 重拍；只验收 `result.json` 明确 complete 的运行。失败阶段可能已有部分 PNG/metadata，不应视为完整数据集。

## Agent 必须知道的 UE 细节

1. 每次重新确认 World、PIE、dirty map、MCP session 和 Slate 引用。不要依据旧对话推断当前地图。
2. `generate_local(True)` / `cleanup_local(True)` 都是延迟操作。控制器先绑定本组件的 Generated/Cleaned/Cancelled delegate，下一 tick 才推进阶段；`IsStreamingCompleted` 不能替代 PCG 完成事件。
3. 首次无资源时 `CleanupLocal` 不发送完成事件。控制器显式区分空组件，避免永远卡在 cleaning。
4. 此版适用于一个受控 PCGVolume，不把 `generated` 用作分区 PCG 的普遍就绪判据；读取 `generation_in_progress` 加上实际数量、组件标签、stencil 和变换检查。
5. UE 5.8 的 `PCGGraphFactory.bSkipTemplateSelection` 没有暴露给 Python。直接 `AssetTools.create_asset` 不经过 UI 的 `ConfigureProperties`。不要猜属性名或触发模板选择弹窗。
6. `EditorAssetLibrary.get_metadata_tag` 在 PIE 中拒绝调用。配方签名同时写入图的反射 `description` 属性，运行时从图对象读取。
7. `auto_semantic_stencil.enabled` 必须为 false。PCG 数据的真值已来自生成规则，启发式推断可能重开有意关闭的 CustomDepth。
8. 专用采集入口串行锁定生成与采集。通用 `capture_once` / `batch_capture` 配合 PCG 配置会因为缺少本次生成证明而拒绝；使用 `capture_pcg_scene.run`。不要绕过检查手填 `_pcg_generation`。
9. 开始采集、真正渲染前和写 metadata 前都再次检查场景；未生成、标签/数量不符、实例变换或 seed 改变、World/已保存图/类别表改变会阻止采集。组件保存本代 token；开始下一代时立即失效旧 token，PIE 重启后也不能拿上一轮证明继续采集，即使 World 路径相同。此检查不是对所有 UE 渲染状态的完整事务锁；仍须避免手动修改、第三方运行时脚本和其他采集入口并发操作。
10. 超时/取消/检查失败会解除 Python delegate/tick 绑定并禁止本次采集。**超时不会取消 UE 底层已排队的任务**；先确认引擎任务停止，必要时结束 PIE/重载地图，再重试。不要热重载活动 job 的模块。中途停止 PIE、外部并发清理及复杂子图取消仍需专项验证。

图形资产和地图保存在外部 UE 工程，源美术来自现有 CitySample；Git 保存可重建脚本和配方，不包含这些二进制资产。非 CitySample 工程需替换源素材和静态底图构造器。当前范围不包括 WP、HiGen、Runtime Generation、GPU、FastGeo、打包游戏或完整城市拓扑。

实测证据、失败记录和参考源码见 [PCG 验证记录](validation/2026-10-10-pcg-baseline.md)。
