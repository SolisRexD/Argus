# CitySample Argus 集成固化设计

## 背景

Argus 已在 UE 5.8 CitySample Photo Mode 中完成玩家自由探索与选择性捕捉：`F9` 会从最终 `PlayerCameraManager` 视角生成 RGB、语义 mask 和 metadata。Argus 代码已经合并到本地 `main`，但 CitySample 不是 Git 仓库，当前三个源码文件和三个资产的集成仍依赖工作站现场状态与手工备份。

本阶段把该集成变成可安装、可验证、可恢复的受控流程，并在验证完成后把 Argus `main` 推送到现有 `origin`。不提交 CitySample `.uasset` 副本，不创建版本标签。

## 目标

- 用一个主机端命令完成 CitySample 集成的安装、验证或恢复。
- 默认使用当前工作站路径，同时允许命令行覆盖 Argus、CitySample 和 UE 5.8 根目录。
- 安装前备份所有将被修改的外部文件，并用 manifest 记录原始与安装后哈希。
- 源码修改可重复执行；已安装状态只验证，不重复插入代码或创建映射。
- 资产修改通过 UE 5.8 Python 在无人工交互的编辑器进程中完成。
- 任一阶段失败时不留下未记录的半安装状态。
- 当前已安装状态可以采用现有的 `20260724_player_capture` 备份建立正式 manifest。
- 完整验证后推送 `main` 到 `origin`，不创建 tag 或 PR。

## 非目标

- 不自动扫描磁盘寻找 UE 或 CitySample。
- 不提供 GUI、编辑器面板、插件安装器或跨平台支持。
- 不把 CitySample 源码全文或 `.uasset` 二进制复制进 Argus Git 仓库。
- 不改动玩家捕捉运行时行为、F9 绑定语义或输出格式。
- 不管理 CitySample 中与 Argus 集成无关的文件，例如现有备份中的 `UnexpectedSaves/BP_CitySamplePC_after_exit_save.uasset`。

## 方案选择

### 采用：主机编排器加 UE 资产脚本

主机端 Python CLI 负责路径、备份、manifest、文本补丁、构建和进程调用；UE Python 脚本只负责加载和修改 Input Action、Mapping Context 与 Blueprint CDO。主机通过 UE 5.8 原生的 `UnrealEditor-Cmd.exe -ExecutePythonScript=...` 启动资产阶段，脚本完成后编辑器自动退出。

这种分层让文本逻辑可在普通 pytest 临时目录中测试，同时让 `.uasset` 始终由 UE 自己读写。

### 未采用：只保存统一文本 patch

文本 patch 无法覆盖 `.uasset` 创建、F9 映射和 CDO 引用，也不能独立验证资产状态。

### 未采用：归档完整 CitySample 文件快照

复制修改后的源码和 `.uasset` 最容易恢复，但会把大型、版本敏感的外部二进制放进 Argus，并可能覆盖用户后续对 CitySample 的合法修改。

## 文件与职责

### `scripts/citysample_argus_integration.py`

提供三个子命令：

- `install`
  - 校验路径、工程文件和目标文件。
  - 对干净工程创建备份与安装中 manifest。
  - 对当前已安装工程，可通过 `--adopt-backup` 采用一份完整的安装前备份。
  - 幂等修改三个 CitySample 源码文件。
  - 构建 `CitySampleEditor Win64 Development`。
  - 调用 UE 资产脚本执行安装和验证。
  - 记录安装后哈希并把 manifest 状态改为 `installed`。
- `verify`
  - 校验 manifest、外部文件哈希和源码契约。
  - 调用 UE 资产脚本只读验证资产状态。
- `restore`
  - 仅接受 manifest 管理且未发生安装后漂移的文件。
  - 恢复三个源码和两个原有资产的字节级备份。
  - 删除 manifest 标记为本次创建的 `IA_PM_ArgusCapture.uasset`。
  - 重新构建未集成 Argus 的 CitySampleEditor，并把 manifest 标记为 `restored`。

默认路径：

- Argus：`D:\Study\Code\Python\UE\cv\Argus`
- CitySample：`E:\UnrealProject\CitySample`
- UE：`E:\UE_5.8`

对应命令行参数为 `--argus-root`、`--citysample-root` 和 `--ue-root`。`verify` 与 `restore` 接受 `--manifest`；未提供时，只在备份根目录中恰好存在一个匹配当前三根路径且状态为 `installed` 的 manifest 时自动选择。除此之外不进行路径自动发现。

### `scripts/citysample_argus_assets.py`

该脚本只在 UE Editor Python 环境中运行，接受 `install` 或 `verify`：

- `install`
  - 不存在时由现有 Boolean Input Action 复制创建 `/Game/Input/PhotoMode/IA_PM_ArgusCapture`。
  - 确认 Value Type 为 Boolean。
  - 确保 `IM_PM_Simple_MappingContext` 中只有一个该 Action 的 `F9` 映射。
  - 设置 `BP_PhotoModeComponent` CDO 的 `capture_action` 引用。
  - 保存三个资产并立即执行同一组断言。
- `verify`
  - 只加载和断言，不保存或修改资产。

脚本输出单一成功标记，并在任何断言失败时让 UE 进程返回非零结果。

### 测试

- 新增纯 Python 测试，覆盖路径解析、manifest、精确锚点补丁、重复安装、采用现有备份、漂移拒绝和恢复。
- 扩展现有 CitySample 源码契约测试，复用同一组期望片段，避免安装器与测试各自维护一份规则。
- UE UObject 操作由 UE 5.8 headless 现场验证覆盖，不另造一套假的资产模型。

## 受管文件

源码：

- `Source/CitySample/Camera/PhotoModeComponent.h`
- `Source/CitySample/Camera/PhotoModeComponent.cpp`
- `Source/CitySample/CitySample.Build.cs`

原有资产：

- `Content/Input/PhotoMode/IM_PM_Simple_MappingContext.uasset`
- `Content/Gameplay/Framework/BP_PhotoModeComponent.uasset`

安装创建的资产：

- `Content/Input/PhotoMode/IA_PM_ArgusCapture.uasset`

任何其他文件都不进入备份、安装或恢复白名单。

## Manifest

manifest 位于 CitySample 外部备份目录，例如：

`ArgusBackups/argus_integration/<timestamp>/manifest.json`

最少记录：

- schema 版本和状态：`installing`、`installed` 或 `restored`
- Argus commit
- 三个根路径
- 创建时间与备份目录
- 每个受管文件的工程相对路径、备份相对路径、原始 SHA-256、安装后 SHA-256
- 文件是原有文件还是安装创建文件
- 已完成的阶段：backup、source、build、assets、verify

采用现有备份时，只接受白名单中的五个原始文件；额外文件被忽略，不写入 manifest。

## 源码修改规则

- 使用现有 CitySample 源码中的精确锚点进行一次性文本插入。
- 如果目标片段已完整存在，则视为已安装并继续验证。
- 如果只存在部分片段、重复片段或锚点不唯一，立即停止，不猜测修复位置。
- C++ Python 命令中的 Argus `scripts` 路径来自 `--argus-root`，统一转换为正斜杠并安全转义。
- 写入采用同目录临时文件加原子替换；保留原编码与换行风格。

## 安装流程

1. 要求 PIE 停止，并要求没有正在使用 CitySample 的 UnrealEditor 进程。
2. 校验根路径、uproject、Build.bat、UnrealEditor-Cmd.exe 和六个目标路径。
3. 干净安装时创建备份并写入 `installing` manifest；当前机器首次固化使用 `--adopt-backup E:\UnrealProject\CitySample\ArgusBackups\20260724_player_capture`。采用模式不改文件，只验证现状和备份后建立 manifest。
4. 幂等应用三个源码修改。
5. 运行 CitySampleEditor 完整构建。
6. 使用 `UnrealEditor-Cmd.exe <uproject> -ExecutePythonScript="<asset-script> install ..."` 创建并验证资产。
7. 记录所有安装后哈希，运行主机与 UE 双层 verify，把 manifest 标记为 `installed`。
8. 运行 Argus 测试、compileall 和 diff 检查。
9. 提交固化实现并推送 `main` 到 `origin`，不创建标签。

## 失败与恢复

- 备份或源码补丁失败：不进入构建，保持原文件不变。
- 构建失败：自动恢复源码备份；资产尚未修改。
- 干净安装的资产阶段失败：等待 UE 进程退出，恢复五个原有文件、删除本次创建的 Action，再重建原始 CitySampleEditor。
- 采用既有安装时验证失败：不修改或恢复现场文件，只保留失败 manifest 和差异报告。
- 自动回滚失败：保留 manifest 和全部备份，报告确切失败阶段与手工恢复命令，不覆盖更多文件。
- `verify` 发现哈希漂移：只报告差异，不修复。
- `restore` 发现安装后漂移：拒绝覆盖，要求先人工处理；首版不提供 `--force`。

## 验收标准

- 在临时目录中，干净安装、重复安装、已有安装采用、失败回滚和恢复测试全部通过。
- 在当前 CitySample 上采用既有备份成功生成 `installed` manifest。
- 主机 verify 通过三个源码契约和全部安装后哈希。
- UE headless verify 确认 Action 为 Boolean、F9 映射唯一、CDO 引用正确。
- CitySampleEditor 构建成功。
- Argus 完整测试、compileall 和 `git diff --check` 通过。
- `AGENTS.md` 保持未跟踪。
- `git push origin main` 成功，`main` 与 `origin/main` 指向同一提交。

## UE 5.8 参考

- `D:\UE58Knowledge\web\markdown\scripting-the-unreal-editor-using-python.md`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Private\EditorUtilities\EditorPythonExecuter.cpp`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Private\PythonScriptCommandlet.cpp`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.h`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.cpp`
- `E:\UnrealProject\CitySample\Source\CitySample\CitySample.Build.cs`
