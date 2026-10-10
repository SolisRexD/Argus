# 干净关卡采集

完整逐脚本流程见 [操作指南](operator_guide.md)，agent 接手和其他 UE 场景适配见 [接手手册](agent_runbook.md)，可纳入版本管理的实验结论见 [验证基线](validation/2026-10-09-capture-baseline.md)。

`/Game/Argus/Maps/Argus_CaptureLab` 是普通非 World Partition 地图，显式使用原生 `GameModeBase`，不需要 Big City 关卡脚本、开场动画或交通生成器。场景约 95×95 米，复用现有静态道路、建筑立面、车辆、树木、长椅和垃圾桶，共 175 个场景网格。相机、RenderTarget、语义材质均在新目录创建。

## 打开和采集

在 UE 5.8 的 Python 控制台执行以下初始化（`Cmd` 模式可在单行命令前加 `py`）：

```python
import sys
sys.path[:0] = [r"D:\Study\Code\Python\UE\cv\Argus", r"D:\Study\Code\Python\UE\cv\Argus\scripts"]
import unreal
unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level("/Game/Argus/Maps/Argus_CaptureLab")
```

先保存正在编辑的地图，再切换关卡。启动 PIE 后执行：

```python
import capture_rgb_and_mask
job = capture_rgb_and_mask.capture_once()
```

任务异步执行，`job.done`、`job.error` 和 `job.result` 可用于检查结果。连续采集前等待上一个任务完成。

```python
import batch_capture
batch = batch_capture.run_batch()
# batch.done 为 True 后可查看 success_count / failed_count。
# 再次调用会跳过已有且文件完整的 capture_id。
```

批次包含斜俯视、垂直俯视和街道视角。自由移动原生 DefaultPawn 后，也可以直接采当前玩家视角：

```python
import capture_player_view
job = capture_player_view.capture_player_view()
```

这个原生 GameMode 没有 CitySample 的 Photo Mode/F9 绑定。默认输出为 `output/capture_lab/captures/` 和 `output/capture_lab/capture_metadata.csv`，分辨率 1920×1080。更改位姿后要换 capture_id 或单独指定输出配置，已有 ID 会被断点续跑跳过。

## 从空白重建与换场景

首次创建时，在 PIE 停止、当前地图已保存的状态执行：

```python
import create_capture_test_level
create_capture_test_level.create()
```

目标地图已存在时脚本会拒绝覆盖；直接打开已有地图即可，也可以显式提供新的 `map_path`。脚本依赖 CitySample 中列出的静态美术资产，175 个网格初始均为 stencil=0，采集时由自动规则标注。固定曝光 EV12、太阳 40,000 lux，避免默认 Lumen 缓存曝光范围溢出。

换成其他已有场景时，不必运行场景创建脚本。复制配置，修改资源目录、输出目录及相机位姿，在编辑模式下依次执行：

```python
import build_semantic_pp_material, setup_dual_capture
build_semantic_pp_material.build_material_and_targets("config/my_scene.json")
setup_dual_capture.setup("config/my_scene.json")
```

保存地图、启动 PIE，再把同一个配置路径传入 `capture_once`、`run_batch` 或 `capture_player_view`。普通已加载场景可使用 `generic`；World Partition 场景还需根据场景流送需求配置玩家流送源移动和等待预算。

新工程需要 Python Script Plugin、Editor Scripting Utilities 以及相关编辑器 API；语义采集要求 Project Settings → Rendering → Custom Depth-Stencil Pass 为 **Enabled with Stencil**（`r.CustomDepth=3`）。Argus 当前不会自动修改这个项目设置。半透明材质还需允许 Custom Depth Writes。自动语义规则根据对象名称和材质推断，其他素材应检查或人工回写真值；程序化天空没有网格 stencil，当前 Mask 中保留背景色。

## 返回 CitySample

原配置保存在 `config/pipeline_citysample.json`。打开 Big City 后，prepare、setup、capture、restore 等入口都传入这个配置；不要把干净关卡位姿用于 Big City。

```python
import prepare_runtime_play_session
prepare_runtime_play_session.prepare("config/pipeline_citysample.json")
# 启动 PIE 后：
job = capture_player_view.capture_player_view("config/pipeline_citysample.json")
# 停止 PIE 后：
import restore_runtime_play_session
restore_runtime_play_session.restore("config/pipeline_citysample.json")
```

现有 F9 集成使用无参默认配置。若继续使用该快捷键，应将默认配置切回 CitySample 预设；否则使用上述显式配置入口。所有入口必须在一次会话内使用一致的预设。

## 验证范围与资源位置

本轮验证包括保存后重载、原生 GameMode PIE、首次自动语义标注、单帧、三视角批量、断点跳过、玩家视角、超时清理及 PIE 重启。场景位于现有 CitySample 工程中，工程级音频/Mass 子系统仍可能启动，尚未验证迁移到独立空工程或打包程序。早先 Big City 会话的 Python GC 崩溃根因仍未确认，本轮稳定运行不等于长期稳定性证明。

地图在 `E:/UnrealProject/CitySample/Content/Argus/Maps/Argus_CaptureLab.umap`，新资源在同工程 `Content/Argus/CaptureLab/`。这些 UE 二进制资产位于外部工程；Argus 仓库保存创建脚本、配置和位姿。验证产物在 `output/validation/clean_level_20261009/`，包括报告、预览、图像统计和运行状态。

实际查阅的 UE 5.8 参考：

- `D:/UE58Knowledge/web/markdown/scripting-the-unreal-editor-using-python.md`
- `D:/UE58Knowledge/web/markdown/setting-up-a-game-mode-in-unreal-engine.md`
- `D:/UE58Knowledge/web/markdown/auto-exposure-in-unreal-engine.md`
- `D:/UE58Knowledge/web/markdown/post-process-effects-in-unreal-engine.md`
- `E:/UE_5.8/Engine/Source/Editor/LevelEditor/Public/LevelEditorSubsystem.h`
- `E:/UE_5.8/Engine/Source/Editor/LevelEditor/Private/LevelEditorSubsystem.cpp`
- `E:/UE_5.8/Engine/Source/Runtime/Engine/Classes/GameFramework/WorldSettings.h`
- `E:/UE_5.8/Engine/Source/Runtime/Engine/Classes/Kismet/GameplayStatics.h`
- `E:/UE_5.8/Engine/Source/Runtime/Renderer/Private/PostProcess/PostProcessEyeAdaptation.cpp`
