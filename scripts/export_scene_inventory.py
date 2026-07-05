"""
导出当前 UE 场景清单。

本脚本是 UE Python 直接运行的入口脚本之一。

它负责：
1. 读取 Argus 配置文件。
2. 扫描当前关卡中的 Actor 和 PrimitiveComponent。
3. 提取 actor / component / mesh / material / instance 信息。
4. 导出 scene_inventory.json。
5. 导出 scene_inventory.csv。

导出的 CSV 后续用于：
- 人工检查场景对象
- LLM 辅助语义清洗
- 生成 semantic_map.csv
"""

import os
import sys


# ---------------------------------------------------------
# 让当前脚本所在目录可以被 Python import
# ---------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


from argus_components import DataPipelineService, SceneObjectCatalog
from common import load_json_config, log, resolve_path, warn


def export_inventory(config_path=None):
    """
    导出当前场景清单。

    参数：
    - config_path:
        配置文件路径。
        如果为空，则由 load_json_config 使用默认配置路径。

    输出：
    - scene_inventory.json
    - scene_inventory.csv

    路径由 cfg["output"] 控制：
    - inventory_json
    - inventory_csv

    如果配置中没有写，则使用默认路径：
    - output/scene_inventory.json
    - output/scene_inventory.csv
    """
    cfg, cfg_path = load_json_config(config_path)

    output_cfg = cfg.get("output", {})

    json_path = resolve_path(
        output_cfg.get("inventory_json", "output/scene_inventory.json")
    )

    csv_path = resolve_path(
        output_cfg.get("inventory_csv", "output/scene_inventory.csv")
    )

    scene_catalog = SceneObjectCatalog()
    pipeline = DataPipelineService()

    rows = scene_catalog.build_inventory_rows()

    if not rows:
        warn("场景清单为空：当前关卡中没有导出到任何 Actor / PrimitiveComponent 记录")

    pipeline.write_scene_inventory(
        rows,
        json_path,
        csv_path,
    )

    log("配置文件: {}".format(cfg_path))
    log("场景清单行数: {}".format(len(rows)))
    log("JSON 已导出: {}".format(json_path))
    log("CSV 已导出: {}".format(csv_path))

    return {
        "rows": len(rows),
        "json_path": json_path,
        "csv_path": csv_path,
    }


if __name__ == "__main__":
    export_inventory()