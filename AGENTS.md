<!-- UE58_LOCAL_DOCS_BEGIN -->
## Unreal Engine 5.8 本地参考资料

- UE 5.8 本地文档知识库：`D:\UE58Knowledge`
- UE 5.8 引擎目录：`E:\UE_5.8`
- 修改 Unreal Engine 相关代码前，先查询本地 UE 5.8 文档，不要只依赖模型记忆。
- 查询准确的类名、函数名、属性名、控制台变量、模块名或插件名时，优先执行：
  `rg -n -i "<标识符或关键词>" "D:\UE58Knowledge"`
- 查询概念、工作流或较宽泛的问题时，执行：
  `& "D:\UE58Knowledge\.venv\Scripts\python.exe" "D:\UE58Knowledge\tools\search_ue58.py" "<查询内容>" --db "D:\UE58Knowledge\ue58_docs.sqlite3" --limit 10`
- 搜索返回结果后，读取相关 Markdown 文件，再开始修改工程。
- 需要确定精确签名、宏、模块依赖或运行时行为时，继续核对 `E:\UE_5.8\Engine\Source` 与 `E:\UE_5.8\Engine\Plugins`。
- 不要用 UE 5.6、UE 5.7 或旧版接口替代 UE 5.8 接口。
- 技术总结中列出实际参考过的本地文档路径或引擎源码文件。
<!-- UE58_LOCAL_DOCS_END -->

## Argus 采集任务入口

- 接手采集、场景适配或故障排查时，先读 `docs/agent_runbook.md`，按需读 `docs/operator_guide.md` 和 `docs/validation/2026-10-09-capture-baseline.md`。
- 默认 `config/pipeline_config.json` 是普通 CaptureLab 的 generic 配置；Big City 使用 `config/pipeline_citysample.json`。所有阶段显式使用同一份配置，不根据旧对话推断当前 World、PIE、MCP session 或准备状态。
- UE 采集依赖编辑器 tick。串行执行采集/批次，不在 UE Python 线程阻塞等待，不在活动任务中热重载、切图或停止 PIE；检查 Job 完成和真实产物之后再推进。
- 人工真值回写与自动语义兜底分开选择：当前自动流程可能重新启用人工关闭的 CustomDepth；人工精确控制时关闭自动兜底。
- 修改前保留已有用户工作。详细实验产物放 `output/validation/`，关键结论、适用边界、实际查阅资料及产物位置另记入 `docs/validation/`，因为 `output/` 被 Git 忽略。
- 区分实际 UE 验证、离线单测和待验证假设；普通新关卡测试不能作为任意工程兼容性或已消除历史崩溃的证明。
