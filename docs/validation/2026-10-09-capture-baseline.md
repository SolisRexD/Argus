# 2026-10-09 采集验证基线

本文件于 2026-10-10 从本机实测报告、JSON、metadata 和日志整理，作为仓库内的持久记录。数值属于 **2026-10-09 已完成的测试**；2026-10-10 的整理工作没有重新执行 UE 采集。详细产物在被 Git 忽略的 `output/validation/`；复制仓库不会自动带走这些产物或外部 UE 资产。

2026-10-10 文档整理已核对 13 个真实脚本入口签名、检查 26 段 Python 示例语法和 18 个本机相对链接，`git diff --check` 通过。这些是文档检查，不是再次执行采集或再次运行 283 项测试。

## 已验证的范围

| 项目 | Big City 修复阶段 | 干净 CaptureLab 阶段 |
| --- | --- | --- |
| 引擎 / 工程 | 本机 UE 5.8 / `E:/UnrealProject/CitySample` | 同一引擎及工程 |
| 地图 | `/Game/Map/Big_City_LVL` | `/Game/Argus/Maps/Argus_CaptureLab` |
| 机制 | CitySample GameMode、WP、Intro/FastGeo 等项目机制 | 普通非 WP 地图，原生 GameModeBase / PlayerController / DefaultPawn |
| 配置存放 | 当前 `config/pipeline_citysample.json` | 当前默认 `config/pipeline_config.json` |
| 输出 | 修复阶段 9 对 RGB/MASK | 6 对 RGB/MASK |
| 分辨率 / 调色板 | 1920×1080；Mask 像素 100% 匹配类别颜色 | 1920×1080；Mask 像素 100% 匹配类别颜色 |
| 自动测试 | 281 passed, 2 skipped | 后续新增测试后 283 passed, 2 skipped |
| 边界 | 有两次早期 Python GC 访问异常，根因未定；最终短回归未复现 | 新关卡短回归、GC、PIE 重启通过，不是长期稳定性证明 |

两个 skip 均为 Windows 缺少符号链接权限的既有集成测试。调色板有效率只证明颜色编码有效，不能替代分类真值或像素级 IoU。当前颜色表中 unknown/umbrella、fx/fountain、background/ignore 有颜色复用。

## 为什么这样修改

| 观察 | 当前处理 / 分析 | 适用边界 |
| --- | --- | --- |
| PIE 内可能选到编辑器世界同名 Actor | PIE 查询限定游戏世界，避免把操作写到编辑器副本 | World 生命周期仍需操作者管理 |
| RGB 偏暗、Mask 需要精确类别色 | RGB 8 位 RT 使用 sRGB/gamma 2.2；Mask 使用 linear/gamma 1.0 | HDR/其他数据流不盲目套用；场景曝光另行检查 |
| 房屋共用铺装材质导致错误类别 | 优先组件/mesh 身份，缓存关键词，保留非零 stencil | 自动推断仍是启发式；错误旧 stencil 也可能被保留 |
| 大场景单次标注阻塞长 | 改为分 tick 标注；generic 256，Big City 4096 个组件/tick | Actor 枚举、排序和导出仍同步 |
| WP 加载与采集位姿不一致 | 指定 pose 并移动玩家流送源时，临时切换 ViewTarget，结束恢复 | 普通关卡不必移动；自定义相机/流送机制需实测 |
| FastGeo/开场机制影响原 Actor 与相机 | CitySample 的 prepare 在 PIE 前执行，保存原值，结束 restore | 不把 CitySample CVar 当作所有场景通用设置 |
| 任务结束后持有 UE 引用 | 完成任务释放闭包，异常路径关闭语义生成器并清理玩家状态 | 不能据此宣称此前 Python GC 崩溃根因已消除 |
| 干净场景 EV13 超出默认 Lumen 缓存安全区间 | 改为 EV12、太阳 40,000 lux，保持曝光/照明比例 | 这是 CaptureLab 的布光选择，不是管线固定要求 |

Big City 首次冷采样约 181 秒，其中流送约 132.5 秒、标注约 42.9 秒（当时每 tick 512 个组件）；热场景在不同前后台状态下明显更快。因此 CitySample 预设超时为 300 秒，不能把一次热采时间当通用预算或简单性能对照。

## CaptureLab 逐项证据

场景约 95×95 米，三栋建筑、三辆静态车、七棵树及道路/长椅等共 175 个网格。资产从现有项目复用；采集相机、RT 和语义材质在 `/Game/Argus/CaptureLab/Capture` 新建。地图文件约 296 KiB，不包含其引用的源素材大小。

| 证据 | 结果 | 本机记录 |
| --- | --- | --- |
| 保存后重载 | 非 WP，原生 GameMode，175 个网格 stencil 都为 0 | `editor_preflight.json` |
| 首帧 `lab_first` | 成功，4.782 秒；175/175 场景网格类别和开关符合清单 | `lab_first_result.json` |
| 三视角批次 | `lab_overview`、`lab_topdown`、`lab_street`：3 成功、0 失败，约 13.8 秒 | `batch_result.json`、metadata、UE 日志 |
| 重跑批次 | 跳过 3 个已完成 ID，无重复 metadata | 同上 |
| 玩家视角 | `lab_20261009_212129` 成功，原生 PlayerCameraManager | `player_result.json` |
| 故意触发超时 | `lab_expected_timeout` 失败；无导出、metadata 不变、未暂停、ViewTarget 恢复 | `cleanup_result.json` |
| 停止 / GC / 重启 | `lab_restart` 再次成功，4.718 秒；重新标注 175/175 符合预期 | `lab_restart_result.json` |
| 最终状态 | PIE 停止，地图保持打开，无 dirty map，175 个编辑器网格仍为 stencil=0 | `final_state.json` |

首次/重启扫描共 203 个组件，185 个写入、18 个忽略、0 写入失败；多于 175 的部分来自运行时/辅助组件，其中 9 个 unknown。175/175 是预先构造的场景网格清单核对，不覆盖任意新素材，也不是逐像素准确率。

六对图像均可解码，1920×1080、alpha=255、Mask 调色板有效率 100%；已人工查看三视角 RGB/Mask 轮廓。全程没有调用 CitySample prepare，Intro/FastGeo 保持原值 1。最终没有 `runtime_play_session_state.json`。

验证脚本最初误用了 Python 未暴露的 `get_pawn`，核对 5.8 源码后改用 `GameplayStatics.get_player_pawn`；这次错误保留在日志中，后续真实采集通过。

## 未完成 / 不能外推的事项

- CaptureLab 仍位于 CitySample 工程中，音频/Mass 等工程级子系统仍可能启动；未验证独立空工程、所有第三方 GameMode/WP 或打包游戏。
- 未完成长期压力、动态场景严格跨流时序一致性、逐像素真值评价。
- 早期两次 Python GC 崩溃有日志/dump，但尚未建立确定因果和消除证明。
- 程序化天空保留背景色；半透明物体是否写 Mask 取决于材质 Custom Depth Writes。
- 2026-10-10 代码审查确认：自动标注可能重新开启人工关闭的 CustomDepth；人工真值流程应关闭自动兜底，当前尚无完整混合策略。
- 全局采集互斥、可靠取消、整个任务的截止时间、配置指纹断点校验、全部 CVar 原值事务恢复尚未实现；由操作者遵守流程并补充记录。详见 [agent 手册](../agent_runbook.md)。

## 产物位置与复跑

- 本机详细修复报告：[repair_report.md](../../output/validation/20261009/repair_report.md)。同目录保存图像统计、两次崩溃日志和最终验证日志。
- 本机干净关卡报告：[report.md](../../output/validation/clean_level_20261009/report.md)。同目录保存以上 JSON、`capture_metadata.csv`、`scene_manifest.json`、`image_analysis.json`、`validation_ue.log`。
- 本机预览：[capture_lab_preview.jpg](../../output/validation/clean_level_20261009/capture_lab_preview.jpg)。实际图像位于 `output/capture_lab/captures/`。
- UE 地图：`E:/UnrealProject/CitySample/Content/Argus/Maps/Argus_CaptureLab.umap`；新资源位于 `Content/Argus/CaptureLab/`。这些文件不在 Argus Git 仓库内。
- 可重建入口：`scripts/create_capture_test_level.py`；配置：`config/pipeline_config.json`；位姿：`config/capture_lab_poses.csv`。仍需要现有 CitySample 美术资产。
- 复跑按 [人工操作指南](../operator_guide.md)；新一轮应另存配置/位姿和输出，避免已有 ID 被续跑跳过。

## 实际参考的本地 UE 5.8 资料

- `D:/UE58Knowledge/web/markdown/world-partition-in-unreal-engine.md`
- `D:/UE58Knowledge/web/markdown/scripting-the-unreal-editor-using-python.md`
- `D:/UE58Knowledge/web/markdown/setting-up-a-game-mode-in-unreal-engine.md`
- `D:/UE58Knowledge/web/markdown/auto-exposure-in-unreal-engine.md`
- `D:/UE58Knowledge/web/markdown/post-process-materials-in-unreal-engine.md`
- `E:/UE_5.8/Engine/Source/Editor/LevelEditor/Public/LevelEditorSubsystem.h`
- `E:/UE_5.8/Engine/Source/Runtime/Engine/Classes/GameFramework/WorldSettings.h`
- `E:/UE_5.8/Engine/Source/Runtime/Engine/Classes/Kismet/GameplayStatics.h`
- `E:/UE_5.8/Engine/Source/Runtime/Engine/Private/PlayerController.cpp`
- `E:/UE_5.8/Engine/Source/Runtime/Engine/Private/TextureRenderTarget2D.cpp`
- `E:/UE_5.8/Engine/Source/Runtime/Renderer/Private/PostProcess/PostProcessEyeAdaptation.cpp`

更完整的实际查阅清单在上述两个本机详细报告中。
