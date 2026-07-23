# Argus 异步 World Partition 捕获设计

## 背景

CitySample 新 PIE 会话中的首次港口捕获虽然已正确挂载语义后处理材质，但仍有约 45.8% 像素落入 `unknown`。同一姿态立即再次捕获时，`unknown` 降至约 0.7%。两次运行分别扫描约 19,984 和 29,329 个组件。

根因是同步 Python 调用阻塞了 UE 游戏线程：移动玩家流送源并调用 `flush_level_streaming()` 后，`time.sleep()` 期间 World Partition 无法继续 Tick、整合新单元；语义 stencil 因而只写入当时已加载的组件。

## 目标

- 新 PIE 会话的第一次捕获也必须等待目标区域流送完成。
- 语义 stencil 必须在流送完成后写入，避免新加载组件保持 unknown。
- RGB、mask 和 metadata 仍按现有规则串行导出。
- 单帧和批量入口都不得阻塞 UE Tick。
- 失败时必须清理 Tick 回调并恢复玩家、暂停和控制台状态。
- 捕获核心以后可由玩家操作的交互式捕捉器直接调用。

## 非目标

- 本阶段不实现可操作 Pawn、HUD、输入映射或拍照提示。
- 不注册第二个主动流送源；只创建一个未注册、transient 的 `WorldPartitionStreamingSourceComponent`，用于按捕获位置查询完成状态。
- 不引入线程、`asyncio`、第三方调度器或自动同步重试。
- 不解决全地图 shader 预编译；此前 `finish_loading_before_screenshot()` 已证明会扩大流送范围并破坏语义写回时序。

## 方案选择

采用 `unreal.register_slate_post_tick_callback()` 驱动一个单帧 `CaptureJob` 状态机。它复用现有玩家流送源，并用 `unreal.new_object(..., outer=capture_actor)` 创建未注册的 transient `WorldPartitionStreamingSourceComponent`。该组件不参与流送源注册，只通过 `is_streaming_completed()` 按捕获 Actor 的位置查询目标网格是否完成。

未采用的方案：

- 生成器调度器：代码可能略短，但结果、异常、取消和清理语义不够直接。
- 注册第二个 World Partition 主动流送组件：可独立驱动另一处加载，但会扩大组件生命周期和状态管理；当前移动玩家流送源已经负责加载，无需再注册一个源。

## 公共接口

`CaptureService.capture_once(cfg, capture_id=None, pose=None)` 保留名称，但改为立即返回 `CaptureJob`，不再同步返回 metadata 字典。

`CaptureJob` 只提供本轮确实需要的状态：

- `done`：是否完成。
- `result`：成功后的 metadata 行。
- `error`：失败异常。
- `add_done_callback(callback)`：完成后通知单帧入口或批量入口。

Job 由已注册的 Slate 回调持有，不要求调用方阻塞等待。结束时先注销回调，再通知完成回调，保证批量入口启动下一帧时不会重叠运行两个 Job。

## 状态与数据流

### 1. 同步启动阶段

调用 `capture_once()` 时只执行不会等待 UE Tick 的工作：

1. 验证 PIE 会话。
2. 查找和配置各 SceneCapture、RenderTarget 与后处理材质。
3. 应用相机姿态和内参。
4. 移动玩家流送源、执行允许的控制台命令并请求 level streaming flush。
5. 以主捕获 Actor 为 outer 创建未注册的 transient 流送查询组件。
6. 注册 Slate post-tick 回调并返回 Job。

### 2. 等待流送

每个 post-tick 调用 transient 查询组件的 `is_streaming_completed()`。至少经过一个回调 Tick，并连续两个 Tick 返回完成后，才进入 warm-up。连续确认用于规避移动流送源后的瞬时旧状态。

若 `runtime.wait_for_streaming` 为 false，则只等待一个 Tick 后继续。

### 3. 非阻塞 warm-up

使用 `time.monotonic()` 累积 `runtime.warmup_seconds`，但不调用 `sleep()`。等待期间持续检查流送状态；若再次变为未完成，则回到流送等待并重新计算 warm-up。

仅在 `waiting` 状态下，等待总时长超过 `runtime.streaming_timeout_seconds` 时失败。默认值为 120 秒，可通过配置调整，避免损坏或异常场景无限挂起；语义扫描和文件导出耗时不属于流送超时。

### 4. 语义写回与捕获

流送稳定且 warm-up 完成后：

1. 根据现有计划暂停游戏（若配置启用）。
2. 执行一次 runtime semantic stencil 写回。
3. 等待下一个 Slate Tick，让组件渲染状态生效。
4. 对每个 stream 保留现有双次 `capture_scene()`。
5. 导出文件、修正 PNG alpha 并构造 metadata 行。

语义写回不会发生在流送等待之前，也不会用同步重试补救遗漏组件。

### 5. 完成与清理

成功或异常都走同一个终止路径：

1. 注销 Slate Tick 回调。
2. 调用现有 `finish_after_capture()` 恢复运行时状态。
3. 设置 `result` 或 `error`，再触发完成回调。

完成回调自身的异常会被记录，但不会再次执行捕获或重复清理。

## 调用方改造

### 单帧入口

`capture_rgb_and_mask.py` 启动 Job 后立即返回。完成回调负责校验文件、追加 metadata 并打印结果。

`capture_pose_probe.py` 在 Job 完成回调中输出 `ARGUS_PROBE_RESULT`，而不是在启动调用后立即读取结果。

### 批量入口

`batch_capture.py` 不再用同步 `for` 循环等待返回值。它保留现有 pose 计划、断点续跑和统计逻辑，通过一个内部 `run_next()` 完成以下串行链：

1. 启动当前 pose 的 Job。
2. 完成后校验文件并追加 metadata。
3. 更新成功或失败计数。
4. 启动下一个 pose。

任何时刻只允许一个捕获 Job 活跃。`continue_on_error` 行为保持不变。

## 错误处理

- 无 PIE 世界、Actor、组件或资产时沿用现有明确异常。
- 无法创建位置查询组件时立即失败，不假装流送完成。
- 流送超时包含 capture id 和等待秒数。
- 所有状态异常都必须进入统一清理路径。
- 批量模式按现有配置选择继续下一帧或终止链。

## 测试与验收

### 自动测试

- Job 启动后立即返回，且只注册一次 post-tick 回调。
- 未完成流送时不执行语义写回或捕获。
- 连续两次完成、非阻塞 warm-up、语义写回和下一 Tick 捕获顺序正确。
- 流送状态回退会重新等待。
- 语义扫描即使跨过流送超时阈值，也会在下一 Tick 正常捕获。
- 成功、捕获异常和超时都会注销回调并恢复状态。
- 批量入口只在上一 Job 完成后启动下一 Job。
- 现有后处理材质回归测试继续要求 `add_or_update_blendable()`。

### UE 5.8 实测

1. 启动全新 CitySample PIE。
2. 直接执行此前失败的港口姿态，只捕获一次。
3. mask 颜色必须全部来自 taxonomy；`unknown` 不得因未流送组件出现大面积占比。
4. 再执行 dense core、freeway、street oblique，确认已有正常场景不回退。
5. 验证批量两帧串行运行且 metadata 与输出文件完整。

## 下一阶段兼容性

玩家操作的交互式捕捉器以后只需在拍摄按键触发时调用同一个 `CaptureService.capture_once()`，并根据 Job 的完成状态显示“流送中 / 捕获成功 / 捕获失败”。本阶段不为该 UI 预建额外接口；只有当交互捕捉器需要在不移动玩家的情况下预加载另一位置时，才注册额外的主动流送组件。

## UE 5.8 参考

- `D:\UE58Knowledge\web\markdown\world-partition-in-unreal-engine.md`
- `D:\UE58Knowledge\web\markdown\using-pcg-generation-modes-in-unreal-engine.md`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Private\PySlate.cpp`
- `E:\UE_5.8\Engine\Plugins\VirtualProduction\VirtualCamera\Content\Python\VCamSmooth.py`
- `E:\UE_5.8\Engine\Source\Runtime\Engine\Public\WorldPartition\WorldPartitionSubsystem.h`
- `E:\UE_5.8\Engine\Source\Runtime\Engine\Private\WorldPartition\WorldPartitionSubsystem.cpp`
- `E:\UE_5.8\Engine\Source\Runtime\Engine\Classes\Components\WorldPartitionStreamingSourceComponent.h`
- `E:\UE_5.8\Engine\Source\Runtime\Engine\Private\Components\WorldPartitionStreamingSourceComponent.cpp`
