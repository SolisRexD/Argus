# 2026-10-10 PCG Lab 实测记录

已实现并在本机 UE 5.8 中运行：CPU、非分区、OnDemand PCG Graph → 清理/生成完成等待 → 实例与语义检查 → 固定场景多视角 RGB/MASK。操作步骤见 [PCG 工作流](../pcg_workflow.md)。这不是对任意 PCG 图、场景或工程的兼容性证明。

## 环境与资产

- 引擎 `E:/UE_5.8`；工程 `E:/UnrealProject/CitySample`；编辑器 PID 16296，MCP 13001 实际调用成功。
- 地图 `/Game/Argus/PCG/Maps/Argus_PCG_Lab`，World Partition 为 None，原生 `/Script/Engine.GameModeBase`。
- 图 `/Game/Argus/PCG/Graphs/PCG_Argus_Lab`：五条 `Create Points Grid → Transform Points → Static Mesh Spawner` 分支，CPU 执行，连接至图输出。
- 原 CaptureLab 保留。新地图包含 157 个明确标注的静态底图网格；PCG 的五个 ISM 组件承载 18 个实例：tree 8、vehicle 4、bench 3、prop 3。
- 采集配置 `config/pipeline_pcg.json`；配方 `config/pcg_lab_recipe.json`；相机使用 `config/capture_lab_poses.csv`。
- `r.CustomDepth=3`；自动语义推断关闭；PCG descriptor 指定 CustomDepth/stencil/类别和来源 tags。无需 PCGPythonInterop，无 CitySample prepare。

## 实际结果

| 检查 | 结果 | 本机证据（`output/validation/pcg_20261010/` 下） |
| --- | --- | --- |
| Python API 探针 | 核心 PCG 类型存在；AddNodeOfType 返回 node/settings；delegate 可绑定 Python callable | `api_probe.json`、`method_probe.json` |
| 首次生成 | 18/18 实例，157/157 底图语义正确 | `editor_seed42.json` |
| 清理再生成 42 → 43 → 42 | 数量稳定；同 seed 的真实世界变换一致；不同 seed 变换不同 | `editor_repeatability.json` |
| 保存地图并重载 | scene hash、数量、stencil 保持一致 | `reloaded.json` |
| 最终代码的 PIE 多视角 | seed 42、43 各三个视角，6 成功、0 失败 | `final_live_result.json`，下方两个最终 run |
| 整个实现阶段的采集 | 三轮双 seed，共 18 对 RGB/MASK；包含代码迭代的中间轮次 | `image_analysis.json`；只把最终一轮视为最终代码验收 |
| PNG 检查 | 全部可解码，RGB/MASK 均 1920×1080、alpha=255、Mask 非调色板像素为 0 | `image_analysis.json`、各 run 的预览 |
| 串行保护 | 生成过程中第二个生成请求被拒绝 | `final_live_result.json` |
| 采集前置保护 | 缺少完成证明、启用自动推断、过期 scene hash、故意将树 stencil 改为 250 均被阻止；无这些测试 ID 的 PNG | `final_live_result.json`；修复标签后检查重新通过 |
| PIE 重启后旧证明 | World 路径和 scene hash 都相同，旧 generation token 仍被拒绝 | `stale_pie.json` |
| 结束状态 | PIE 停止、seed 42、157 静态网格、18 实例、5 ISM、无 dirty map | `final_state.json`、`editor_final_manifest.json` |
| Python 自动测试 | **313 passed, 2 skipped** | `python -m pytest -q`；两个 skip 为既有 Windows symlink 权限测试 |

同 seed 的 scene hash 不受 UE 临时组件名称、枚举顺序和实例索引影响：

- seed 42：`3fdb08088792bb7003d893634d144a0bc20a21b417cc5161064fa14ef838aec9`
- seed 43：`877683bb33be2f5ac562e34c3440f3512c90de16d0f5d58f746651b9cb22a056`

编辑器重复生成的三次耗时约 0.55、1.00、0.67 秒；只是本机这个小图的结果，不是一般性能承诺。视角切换沿用现有 streaming/warmup/pause/恢复流程。最终 metadata 中每帧带 generation ID、seed、scene/recipe/saved graph SHA；生成清单另存完整类别 CSV 指纹和逐实例数据。

已查看 RGB/MASK 拼图：建筑、路面、车辆、树和小道具的主体类别及轮廓对应；高位视图包含全部 PCG 类别。未做人工逐像素真值或 IoU。当前调色板存在 background/ignore 等复用，统计中的黑色不能单凭颜色区分两者；程序化天空保持背景。

## 实际踩到并处理的问题

1. 工厂头文件存在 `bSkipTemplateSelection`，但 Python 找不到该属性。实际 `AssetTools.create_asset` 直接调用 `FactoryCreateNew`，建图未出现模板弹窗。
2. `EditorAssetLibrary.get_metadata_tag` 在 PIE 中返回失败并记录 “The Editor is currently in a play mode”，导致最初运行被配方检查阻止。改用图本身的反射 `description` 属性读取签名后通过；初始失败现场保留。
3. 修改后端模块后，生成控制器通过 `from ... import ...` 保留了旧函数引用，第二次尝试仍触发旧接口。仅在所有 job 结束、停止 PIE 后重载依赖模块，后续运行通过。正常用户新会话不需要这套开发热重载操作。
4. 负面验证脚本最初只捕获 RuntimeError，实际错误标签检查抛 ValueError；标签在 finally 中恢复，采集本身已成功。修复测试脚本捕获范围，最终重新执行整轮并得到完整报告。
5. World 路径在 PIE 重启后会复用，单凭路径和布局 hash 不能判定是本轮生成。新增组件 generation token，生成开始立即清除旧 token，完成后再颁发新 token；实测旧证明被拒绝。
6. UE 保存日志有“无法从版本控制检出”的项目提示；保存返回成功、文件存在、重新载入一致。本次 Argus Git 上传的是脚本/配置/文档，不是 UE 工程的源控制状态。

## 边界与后续工作

- 此版底图仍固定；不同 seed 改变的是树/车/道具的变换。下一阶段才扩展道路、地块、楼层和素材集合。当前配方未实现通用碰撞/退让约束。
- 同 mesh 的两条树木分支已保持独立 provenance；**同 mesh 分配不同语义类别、允许组件合并后的情况尚未专门实测**。
- 空组件首次生成、延迟事件、取消/超时、失败解绑、并发拒绝在单测中覆盖；UE 实测覆盖正常清理/生成及上述采集前置失败，未实际强制 PCG 取消或超时。超时不会取消底层 UE 任务，恢复方式见工作流。
- 没有验证 WP、HiGen、Runtime Generation、GPU、FastGeo、任意蓝图生成、打包游戏或独立空工程。CitySample 音频等工程级子系统仍运行。
- generation token、场景清单和 hash 检查不是全局引擎事务锁，也不完整覆盖材质、可见性、所有渲染设置及源素材版本。禁止采集中手工编辑或启动其他采集入口。
- 不提供旧目录断点恢复；每个 run 独立输出，防止不同 seed 误用旧 capture ID。失败运行可能保留部分产物。
- 既有 Big City Python GC 崩溃根因仍未确定；本次多次 PIE 启停和 GC 后成功不能证明长期稳定性。

## 产物与复跑

最终代码验收的两个运行：

- `output/pcg_lab/runs/20261010T084418Z_seed42_c0c38771/`
- `output/pcg_lab/runs/20261010T084434Z_seed43_8d467716/`

探针、重复性检查、保护检查、最终状态、拼图及 UE 日志在 `output/validation/pcg_20261010/`；`output/` 被 Git 忽略。地图/图/采集资产在 `E:/UnrealProject/CitySample/Content/Argus/PCG/`；现有 CitySample 源美术和 CaptureLab 材质被引用，均不随 Argus Git 上传。仓库脚本可在具有这些美术资源的工程内重建；已有资产拒绝覆盖。

复跑使用 [PCG 工作流](../pcg_workflow.md)，关注返回 job 的实际完成状态及 `result.json`，不要根据控制台工具返回 true 推断采集完成。

## 本次实际查阅的 UE 5.8 资料

- `D:/UE58Knowledge/web/markdown/procedural-content-generation-overview.md`
- `D:/UE58Knowledge/web/markdown/using-pcg-generation-modes-in-unreal-engine.md`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCG/Public/Elements/PCGCreatePointsGrid.h`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCG/Private/Elements/PCGCreatePointsGrid.cpp`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCG/Public/Elements/PCGTransformPoints.h`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCG/Public/MeshSelectors/PCGMeshSelectorWeighted.h`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCG/Public/PCGComponent.h`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCG/Private/PCGComponent.cpp`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCGEditor/Private/PCGGraphFactory.h`
- `E:/UE_5.8/Engine/Plugins/PCG/Source/PCGEditor/Private/PCGGraphFactory.cpp`
- `E:/UE_5.8/Engine/Source/Developer/AssetTools/Private/AssetTools.cpp`
- `E:/UE_5.8/Engine/Source/Runtime/Engine/Private/ISMPartition/ISMComponentDescriptor.cpp`

另外，真实 Python 反射探针是本次 API 签名/属性确认的直接依据；前置调研的其他参考资料见 [研究记录](../research/ue58_pcg_argus.md)。
