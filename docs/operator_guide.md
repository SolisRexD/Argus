# 人工操作：逐脚本采集指南

本文按当前代码的真实入口编写。已有实验结论见 [验证基线](validation/2026-10-09-capture-baseline.md)，UE 机制与排障见 [agent 手册](agent_runbook.md)。字段细节仍以 [工作流参考](workflow.md) 为准。

## 1. 先选配置和执行环境

| 场景 | 配置 | 位姿 / 输出 | 启动要求 |
| --- | --- | --- | --- |
| 普通测试关卡 Argus_CaptureLab | `config/pipeline_config.json` | `capture_lab_poses.csv` / `output/capture_lab/` | 直接 PIE |
| CitySample Big City | `config/pipeline_citysample.json` | `camera_poses.csv` / `output/` | PIE 前 prepare，停止后 restore |
| 新场景 | 复制普通配置，另存为 `config/my_scene.json` | 为该场景单独设置 | 根据实际流送 / GameMode 机制调整 |

复制新配置时一起修改 `assets.root`、`semantics.semantic_map_csv`、`output` 中的所有路径、`batch.poses_csv` 和相机位姿。不同场景或不同标注版本不要共用 metadata 和 capture_id。JSON 中路径相对 **Argus 根目录** 解析，不相对配置文件目录；`capture_pose_probe` 的特殊路径语义见末尾表格。

下面的 Python 命令在 **UE Editor 的 Python 控制台**运行。输出日志若选的是 `Cmd`，单行前加 `py`。也可保存小脚本后通过 `py "绝对路径.py"` 执行。包含 `unreal` 的入口不能直接在系统 PowerShell 中用 `python script.py` 运行；系统 Python 用于 pytest、配置编辑和离线图像检查。Python 插件不意味着可在打包游戏里运行这套编辑器流程。

UE Python 中初始化一次：

```python
import sys
ROOT = "D:/Study/Code/Python/UE/cv/Argus"
sys.path[:0] = [ROOT, ROOT + "/scripts"]
CFG = "config/pipeline_config.json"
import unreal
```

所有后续入口显式传同一个 `CFG`。无参入口使用 `pipeline_config.json`，不会根据地图自动选配置。

## 2. 打开场景，选择标注方式

先保存正在编辑的地图、停止 PIE，再打开目标地图。已有实验关卡可以执行：

```python
unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level("/Game/Argus/Maps/Argus_CaptureLab")
```

首次创建实验关卡才运行 `import create_capture_test_level; create_capture_test_level.create(CFG)`；它会创建场景、材质、RenderTarget 和相机，已有地图会拒绝覆盖。后续直接打开即可。场景创建依赖现有 CitySample 静态美术资产，并非任意工程都带有这些资源。

检查项目 Custom Depth-Stencil Pass 为 **Enabled with Stencil**：

```python
print(unreal.SystemLibrary.get_console_variable_int_value("r.CustomDepth"))  # 应为 3
```

Argus 不自动更改这个项目设置。确认插件和设置就绪后，选一种标注方式：

| 方式 | 配置 / 操作 | 适用情况 |
| --- | --- | --- |
| 自动规则试采 | `runtime.auto_semantic_stencil.enabled=true`，可跳过下一节的 CSV 回写 | 快速验证相机、渲染和语义链路；结果要抽查 |
| 人工真值回写 | 在该场景配置中设 `runtime.auto_semantic_stencil.enabled=false`，执行下一节 | 已审阅的类别、忽略对象、精确开关控制 |

**当前自动标注会为保留的非零 stencil 重新启用 CustomDepth，也会处理其他未标注组件。** `preserve_existing=true` 只保留非零类别值，不保护人工设置的 `render_custom_depth=false`。人工真值流程应关闭自动兜底；混用需要额外设计对象排除规则，当前尚未实现。

## 3. 人工真值流程：导出 → 清洗 → 校验 → 回写

在编辑模式处理需要持久保存的静态场景。World Partition 的清单只覆盖当前加载对象，不能当作整张地图的完整清单；运行时生成对象需要在 PIE 中单独处理，停止 PIE 后修改不会自动回写到编辑器地图。

先导出：

```python
import export_scene_inventory
inventory = export_scene_inventory.export_inventory(CFG)
print(inventory)
```

依据清单填写配置中 `semantics.semantic_map_csv` 指定的文件。`config/semantic_map_template.csv` 是字段和示例，不能原样当作当前关卡的规则。使用真实 Actor Label、组件名以及需要的 mesh/material 消歧字段；类别 / stencil 取自 `config/semantic_classes.csv`。当前没有自动把清单转换成经过审阅真值的脚本，这一步由人或 agent 编写并检查。

校验：

```python
import validate_semantic_map
validation = validate_semantic_map.validate_semantic_map(CFG)
print(validation)
assert validation["errors"] == 0
```

函数返回统计，不因所有语义问题自动抛异常。还要打开校验 CSV 检查 warning：`invalid_render_switches`、`requires_material_split`、`requires_instance_split` 等会导致规则无法按预期执行，不能只看 errors=0。

演练回写：

```python
import writeback_semantic_stencil
dry = writeback_semantic_stencil.writeback(CFG, dry_run=True)
print(dry["status_counter"])
```

检查 `writeback_log_csv` 的匹配对象、策略和状态，修正缺失或歧义规则后正式回写：

```python
written = writeback_semantic_stencil.writeback(CFG, dry_run=False)
print(written["status_counter"])
```

直接执行 `writeback_semantic_stencil.py` 的默认行为是**正式写入**，不是 dry run。演练和正式回写使用同一路径写日志，需对比时先另存演练日志。确认实际写入后在编辑器保存目标地图及改动的外部 Actor 包；PIE 副本中的写入只对本次运行有效。

## 4. 构建采集资源并保存

新的普通场景先执行：

```python
import build_semantic_pp_material, setup_dual_capture
build_semantic_pp_material.build_material_and_targets(CFG)
setup_result = setup_dual_capture.setup(CFG)
print(setup_result)
```

构建器创建 / 更新语义材质及 RenderTarget；setup 创建 / 更新配置标签对应的 SceneCapture Actor。确认 `SC_RGB`、`SC_MASK` 指向正确资产，保存地图。已有 CaptureLab 首次创建时已做过这两步；重跑 setup 会应用配置中的相机设置，不要把它当只读检查。

改变类别颜色表后重建后处理材质；改变分辨率、资产路径或相机配置后重新 setup 并检查。不要在正在执行采集的任务中重建材质或 RenderTarget。

## 5. 启动与单帧

普通 `generic` 可以直接点击 **Play**。CitySample 必须在 Play 之前执行：

```python
import prepare_runtime_play_session
prepare_runtime_play_session.prepare(CFG)
```

它为 CitySample 在 PIE 世界创建前处理 Intro/FastGeo，记录 PID 和原值。不要先启动 PIE 再补执行；新建编辑器进程后也不能复用旧 PID 的准备记录。

启动 PIE 后采一帧：

```python
import capture_rgb_and_mask
job = capture_rgb_and_mask.capture_once(CFG)
```

脚本返回 **CaptureJob**，不是已完成的 metadata。把控制权交还编辑器，让它继续 tick；隔几秒在下一条命令中查看：

```python
print(job.done, job.error)
if job.done and job.error is None:
    print(job.result)
```

不要在 UE Python 中写 `while not job.done` 或用 `time.sleep()` 阻塞等待，这会阻止任务推进。一次只运行一个采集或批次。`capture_once` 不传 pose 时使用当前 SceneCapture 位姿，并非当前玩家视角；需要当前玩家相机时改用：

```python
import capture_player_view
job = capture_player_view.capture_player_view(CFG)
```

它从 PlayerCameraManager 读取最终位姿/FOV。CaptureLab 没有 CitySample Photo Mode/F9 输入；现有 CitySample F9 绑定调用无参入口，所以使用 F9 前需让默认配置与该场景一致。

## 6. 验收第一帧再跑批量

确认 job 完成且 error 为空后，打开图像，检查两路尺寸、RGB 内容、Mask 类别和轮廓；核对 metadata 中的位姿、FOV、文件路径、`runtime_plan_json` 和 `semantic_stencil_json`。文件存在只能说明导出成功；颜色都在调色板内也不能证明类别正确。

先用少量已知视角：

```python
import batch_capture
batch = batch_capture.run_batch(CFG)
```

稍后单独查询：

```python
print(batch.done, batch.success_count, batch.failed_count, batch.error)
```

默认 `continue_on_error=true`，必须检查 failed_count，不能只看 done 或 error。再次运行会按 capture_id 和必要文件存在性跳过已有帧；它**不比较位姿 / 配置哈希，不验证 PNG 像素内容**。修改位姿、类别或渲染设置后使用新的 ID 或独立输出目录。不要为重拍直接删除旧实验。

默认 `clean_incomplete_metadata=true` 会重写 metadata，移除不完整记录。需要保留失败现场时先复制该 CSV；不要同时启动两个写同一 CSV 的进程。

## 7. 结束与留档

等任务 / 批次完成，停止 PIE。**如果本次调用过 prepare**，再执行对应 restore：

```python
import restore_runtime_play_session
restore_runtime_play_session.restore(CFG)
```

直接启动的 generic 会话没有准备状态文件，不需要 restore；无状态却调用 restore 可能报错。CitySample 应确认原始 Intro/FastGeo 值恢复，`output/runtime_play_session_state.json` 已清除。

保存配置和位姿快照、采集 ID、图片预览、metadata、相关 UE 日志及成功/失败统计。详细产物放 `output/validation/<日期或实验名>/`；该目录被 Git 忽略，因此关键结论、限制及产物位置还要记录到 `docs/validation/`。

## 入口索引

`CFG` 表示同一份 pipeline 配置；除 `citysample_argus_integration.py` 主程序外，下列入口都需要 UE Editor Python 环境。

| 脚本 / 调用 | 执行时机 | 结果与副作用 |
| --- | --- | --- |
| `create_capture_test_level.create(CFG, map_path=...)` | PIE 前，可选 | 新地图、场景及采集资产；已有目标拒绝覆盖 |
| `export_scene_inventory.export_inventory(CFG)` | 目标 World 对象已加载 | 清单 JSON/CSV；不生成真值规则 |
| `validate_semantic_map.validate_semantic_map(CFG)` | 规则文件存在 | 校验 CSV、total/errors/warnings；只读场景 |
| `writeback_semantic_stencil.writeback(CFG, dry_run=True/False)` | 校验后 | 演练或写组件；日志和 status_counter |
| `build_semantic_pp_material.build_material_and_targets(CFG)` | PIE 前 | 更新并保存材质 / RenderTarget；以日志和资产检查确认 |
| `setup_dual_capture.setup(CFG)` | PIE 前 | 更新相机；需保存地图 |
| `prepare_runtime_play_session.prepare(CFG)` | PIE 前 | 状态计划、CVar 和会话文件 |
| `capture_rgb_and_mask.capture_once(CFG, capture_id=..., pose=...)` | PIE 中 | 异步 CaptureJob；图像及 metadata |
| `capture_player_view.capture_player_view(CFG)` | PIE 中有本地玩家 | 异步 CaptureJob；当前玩家最终视角 |
| `batch_capture.run_batch(CFG)` | 首帧验收后 | 异步 BatchCaptureRunner；串行多帧、断点跳过 |
| `restore_runtime_play_session.restore(CFG)` | 停止 PIE 后且已 prepare | 恢复准备阶段记录的受支持设置 |
| `diagnose_runtime_stencils.main()` | 诊断当前 World | 返回报告列表并打印分布 / 样例，不修改 stencil；诊断推断不是所选配置完整重放 |
| `capture_pose_probe.main(probe_json_path)` | 调试、没有其他任务时 | 参数是**探针 JSON**，建议传绝对路径；不是 pipeline 配置且不走 pipeline 的根目录解析；内部仍用默认 pipeline，并清理模块缓存；一般优先显式调用 capture_once |
| `citysample_argus_integration.py install/verify/restore` | 系统 Python，关闭 UE | 专用 CitySample F9 集成与 manifest，不是普通场景的必经步骤 |
| `citysample_argus_assets.py` | 集成流程在 UE Python 环境中调用 | UE 资产桥接辅助，按 integration 文档调用，不作为通用采集入口 |

额外风险：导出或清理阶段失败时可能已有部分文件，甚至已经写入 metadata；不能把“job 失败”一律理解为无产物。先核对现场，再决定如何续跑。
