# Argus 玩家操作式捕捉器设计

## 背景

Argus 已完成非阻塞 World Partition 单帧与批量捕捉，并通过 Harbor、Dense Core、Freeway 和 Street Oblique 实测。下一阶段需要让使用者在 CitySample 场景中自由探索，并在当前视角选择性生成同步 RGB、语义 mask 和 metadata。

CitySample 已经提供完整 Photo Mode：自由飞行、视角控制、升降、自动对焦和隐藏 UI。Argus 也已经提供可复用的 `CaptureService.capture_once()` 与单活动 `CaptureJob` 状态机，因此本功能不再创建 Pawn、相机系统、HUD 或第二套调度器。

## 目标

- 使用者进入 CitySample Photo Mode 后继续使用原有移动和相机控制。
- 按 `F9` 时，从 `PlayerCameraManager` 读取最终可见相机的位置、旋转和水平 FOV。
- 使用该位姿调用现有 Argus 异步单帧捕捉，输出 RGB、mask 和 metadata。
- 捕捉期间不移动 Photo Mode Pawn；玩家本身继续作为目标位置的 World Partition 流送源。
- 同一时刻最多保留一个交互捕捉 Job，重复按键只显示“正在捕捉”。
- 用 UE 屏幕消息和日志显示开始、成功 capture ID 或失败原因。

## 非目标

- 不支持打包后的 Shipping/Development 游戏；首版只支持 Unreal Editor 中的 PIE。
- 不创建新 Pawn、自定义 HUD、拍照菜单、相册、连拍、排队或取消功能。
- 不修改现有批量捕捉行为，也不把全局配置的 `move_player_to_capture` 改为 `false`。
- 首版只增加键盘 `F9`；手柄映射在确有使用需求时再加。

## 方案选择

### 采用：扩展现有 Photo Mode 输入并调用 Argus Python

在 `UPhotoModeComponent` 中增加一个 `CaptureAction`，沿用现有 Enhanced Input 绑定方式。按键处理器只在 Editor 构建中通过 `IPythonScriptPlugin` 执行很短的 Python 导入和调用命令。输入、移动和相机仍完全由 CitySample 负责，捕捉仍完全由 Argus 负责。

这是改动最少、行为最贴近现有 Photo Mode 的方案。

### 未采用：只改 Blueprint

Blueprint 可以调用 `Execute Python Command`，但自动修改现有组件 Blueprint 图、错误处理和版本复现比一处小型 C++ 绑定更脆弱，也不利于文本审查。

### 未采用：新建 Argus UE 插件或 Pawn

独立插件更适合可分发产品，但首版需要自行接管 PIE 生命周期、玩家控制器发现和输入上下文；新 Pawn 还会重复 CitySample 已有的 Photo Mode。当前需求不值得这些额外边界。

## 架构与文件职责

### Argus 仓库

- `scripts/capture_rgb_and_mask.py`
  - 将现有“按配置完成单帧捕捉和 metadata 写入”的部分提取成一个可复用函数。
  - 原 `capture_once(config_path=None, capture_id=None, pose=None)` 保持兼容。
- `scripts/capture_player_view.py`
  - 获取 PIE 世界、0 号本地玩家控制器和 `PlayerCameraManager`。
  - 生成包含 `x/y/z/pitch/yaw/roll/fov_deg` 的 pose。
  - 加载默认配置，并仅对本次调用设置：
    - `runtime.move_player_to_capture = false`
    - `runtime.restore_player_after_capture = false`
  - 保留模块级 `_active_job`，阻止重叠捕捉。
  - 通过 Job 完成回调显示结果并释放 `_active_job`。
- `tests/test_capture_player_view.py`
  - 用最小假对象覆盖相机位姿、FOV、配置覆盖、重入保护和完成状态。
- `tests/test_async_capture_entrypoint.py`
  - 保持原入口行为，并验证提取后的共享 finalize 路径。

### CitySample 工程

- `Source/CitySample/Camera/PhotoModeComponent.h/.cpp`
  - 增加 `CaptureAction` 属性与 `CaptureActionBinding()`。
  - 在现有 `SetUpInputs()` 中按 `ETriggerEvent::Started` 绑定。
  - 仅在 `WITH_EDITOR` 下调用 `IPythonScriptPlugin`；非 Editor 构建记录不可用，不引用 Python 模块。
  - Python 命令把当前 Argus `scripts` 目录加入 `sys.path`，随后导入并调用 `capture_player_view.capture_player_view()`。本机路径直接写入这一处集成代码；需要跨机器分发时再提升为项目设置。
- `Source/CitySample/CitySample.Build.cs`
  - 仅在 `Target.bBuildEditor` 时添加 `PythonScriptPlugin` 私有依赖。
- `/Game/Input/PhotoMode/IA_PM_ArgusCapture`
  - 新建 Boolean Input Action。
- `/Game/Input/PhotoMode/IM_PM_Simple_MappingContext`
  - 将 `F9` 映射到 `IA_PM_ArgusCapture`。
- `/Game/Gameplay/Framework/BP_PhotoModeComponent`
  - 将新增 `CaptureAction` 属性指向 `IA_PM_ArgusCapture`。

CitySample 目录不是 Git 仓库。实施前对所有被替换的源码和 uasset 建立带时间戳的本地备份；Argus 仓库中的规格、计划和 Python 代码仍由 Git 跟踪。

## 数据流

1. 使用者进入 Photo Mode；原有 `IM_PM_Simple_MappingContext` 被激活。
2. 使用者按 `F9`；`UPhotoModeComponent::CaptureActionBinding()` 执行 Argus Python 命令。
3. `capture_player_view.capture_player_view()` 检查是否已有未完成 Job。
4. Python 从 `PlayerCameraManager` 调用 `GetCameraLocation()`、`GetCameraRotation()` 和 `GetFOVAngle()`，构造当前可见相机 pose。
5. Python 加载默认 pipeline 配置，只在内存中关闭本次玩家移动，然后调用共享单帧入口。
6. `CaptureService` 把 `SC_RGB` 和同步 stream 移到玩家相机 pose，按该位置查询 World Partition 流送完成状态，完成语义写回、渲染和导出。
7. 现有 finalize 路径验证文件、追加 metadata；完成回调显示 capture ID 或错误，并清除活动 Job。

## 状态与错误处理

- 无 PIE 世界、无本地玩家控制器、无 `PlayerCameraManager`：不启动 Job，屏幕和日志显示明确错误。
- Python 插件未初始化：Editor 绑定先请求初始化；命令失败时由 CitySample 日志报告。
- 已有未完成 Job：不排队、不创建第二个 Job，只显示当前正在捕捉。
- 同步启动异常：立即清除活动状态、显示错误并让 Python 命令返回失败。
- 异步 Job 失败：读取 `job.error`，显示错误并清除活动状态；底层 Job 继续使用现有统一清理路径。
- 成功：显示 `Argus captured: <capture_id>`；metadata 和文件校验继续由现有 finalize 负责。

## 测试与验收

### 自动测试

- 相机位置、旋转和 FOV 原样进入 pose。
- 交互配置只关闭玩家移动，不修改磁盘配置或批量入口默认值。
- 活动 Job 未完成时第二次触发不会再次调用 `CaptureService`。
- 成功、异步失败和同步启动失败都会显示状态并释放活动 Job。
- 现有完整 Python 测试集、`compileall` 和 `git diff --check` 继续通过。
- CitySample Editor 目标重新编译成功。

### UE 5.8 实测

1. 加载 `IA_PM_ArgusCapture`、mapping context 和 `BP_PhotoModeComponent` CDO，确认引用和 `F9` 映射。
2. 启动 CitySample PIE 并进入 Photo Mode，确认原移动、视角、升降和自动对焦仍正常。
3. 在两个不同位置和朝向分别触发捕捉，确认每次只新增一条 metadata，RGB 与 mask 文件存在。
4. 对照触发瞬间的 `PlayerCameraManager`，确认 metadata 位姿和 FOV 与玩家最终视角一致。
5. 在 Job 未完成时再次触发，确认没有第二个活动 Job 或重复 metadata。
6. 检查 mask 只包含 taxonomy 颜色，invalid pixels 为 0；记录 unknown 比例但不把场景本身的天空或远景 unknown 当作输入功能失败。

## UE 5.8 参考

- `D:\UE58Knowledge\web\markdown\city-sample-project-unreal-engine-demonstration.md`
- `D:\UE58Knowledge\web\markdown\cameras-in-unreal-engine.md`
- `D:\UE58Knowledge\web\markdown\scripting-the-unreal-editor-using-python.md`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.h`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.cpp`
- `E:\UnrealProject\CitySample\Source\CitySample\CitySample.Build.cs`
- `E:\UE_5.8\Engine\Source\Runtime\Engine\Classes\Camera\PlayerCameraManager.h`
- `E:\UE_5.8\Engine\Source\Runtime\Engine\Private\PlayerCameraManager.cpp`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\PythonScriptPlugin.uplugin`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Public\IPythonScriptPlugin.h`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Private\PythonScriptLibrary.h`
