"""
将 semantic_map.csv 中的语义规则写回到 UE 组件。

本脚本是 UE Python 直接运行的入口脚本之一。

它负责：
1. 读取 Argus 配置文件。
2. 读取 semantic_map.csv。
3. 扫描当前 UE 场景并构建组件索引。
4. 根据 semantic_map.csv 规则定位对应组件。
5. 写入组件渲染开关：
   - render_in_main_pass
   - render_custom_depth
6. 写入组件 Custom Stencil：
   - custom_depth_stencil_value
7. 输出 stencil_writeback_log.csv。

注意：
- 真正的回写逻辑在 AnnotationController 中。
- 本脚本只负责读取配置、调度组件、保存日志。
- 建议正式回写前先运行 validate_semantic_map.py。
"""

import os
import sys


# ---------------------------------------------------------
# 让当前脚本所在目录可以被 Python import
# ---------------------------------------------------------
# UE Python 执行脚本时，sys.path 不一定包含当前脚本目录。
# 所以这里手动加入 SCRIPT_DIR，保证可以 import:
# - argus_components
# - common
# - 其他同目录模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

for path in [PROJECT_ROOT, SCRIPT_DIR]:
    if path not in sys.path:
        sys.path.insert(0, path)


from argus_components import AnnotationController, DataPipelineService, SceneObjectCatalog
from common import load_json_config, log, resolve_path, semantic_map_to_stencil, warn


def _count_status(log_rows):
    """
    统计 writeback 日志中的 status 数量。

    返回：
    {
        "ok": 10,
        "component_not_found": 2,
        ...
    }
    """
    counter = {}

    for row in log_rows:
        status = str(row.get("status", "")).strip() or "<empty>"
        counter[status] = counter.get(status, 0) + 1

    return counter


def _log_status_summary(counter):
    """
    打印 writeback status 汇总。
    """
    if not counter:
        log("Writeback status 汇总为空")
        return

    log("Writeback status 汇总:")

    for status in sorted(counter.keys()):
        log("  {}: {}".format(status, counter[status]))


def writeback(config_path=None, dry_run=False):
    """
    执行语义回写。

    参数：
    - config_path:
        配置文件路径。
        如果为空，则由 load_json_config 使用默认配置路径。

    - dry_run:
        是否只演练，不真正修改 UE 场景。

        dry_run=True：
            只解析规则、匹配组件、生成日志，不写入组件属性。

        dry_run=False：
            真正写入：
            - render_in_main_pass
            - render_custom_depth
            - custom_depth_stencil_value

    返回：
    - result:
        包含规则数、日志路径、状态统计等信息。
    """
    cfg, cfg_path = load_json_config(config_path)

    sem_cfg = cfg["semantics"]
    output_cfg = cfg["output"]

    semantic_map_csv = resolve_path(sem_cfg["semantic_map_csv"])
    class_table_csv = resolve_path(sem_cfg["class_table_csv"])

    log_csv = resolve_path(
        output_cfg.get("stencil_writeback_log", "output/stencil_writeback_log.csv")
    )

    rules = semantic_map_to_stencil(semantic_map_csv)

    if not rules:
        warn("semantic_map.csv 没有读取到任何有效规则: {}".format(semantic_map_csv))

    scene_catalog = SceneObjectCatalog()
    component_index = scene_catalog.build_component_index()

    annotator = AnnotationController()

    logs = annotator.apply_writeback(
        rules=rules,
        sem_cfg=sem_cfg,
        class_table_csv=class_table_csv,
        scene_catalog=scene_catalog,
        component_index=component_index,
        dry_run=dry_run,
    )

    pipeline = DataPipelineService()
    pipeline.write_stencil_writeback_log(log_csv, logs)

    status_counter = _count_status(logs)

    log("配置文件: {}".format(cfg_path))
    log("语义回写完成: dry_run={}".format(dry_run))
    log("语义规则数量: {}".format(len(rules)))
    log("回写日志数量: {}".format(len(logs)))
    log("Semantic map: {}".format(semantic_map_csv))
    log("Writeback log CSV: {}".format(log_csv))

    _log_status_summary(status_counter)

    return {
        "dry_run": dry_run,
        "rules": len(rules),
        "logs": len(logs),
        "semantic_map_csv": semantic_map_csv,
        "writeback_log_csv": log_csv,
        "status_counter": status_counter,
    }


if __name__ == "__main__":
    writeback(dry_run=False)
