"""
构建语义分割后处理材质和所需 RenderTarget。

本脚本是 UE Python 直接运行的入口脚本之一。

它负责：
1. 读取 Argus 配置文件。
2. 根据 semantic_classes.csv 生成 stencil -> color 的语义颜色映射。
3. 创建或更新语义 mask 后处理材质。
4. 创建或更新 RGB / MASK RenderTarget。
5. 打印构建结果。

注意：
- 真正的材质图生成逻辑在 SemanticPostProcessBuilder 中。
- 本脚本只负责读取配置、调用构建器、输出日志。
"""

import os
import sys


# ---------------------------------------------------------
# 让当前脚本所在目录可以被 Python import
# ---------------------------------------------------------
# UE Python 执行脚本时，sys.path 不一定包含当前脚本目录。
# 所以这里手动把 SCRIPT_DIR 加入 sys.path，保证可以 import:
# - argus_components
# - common
# - 其他同目录模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

for path in [PROJECT_ROOT, SCRIPT_DIR]:
    if path not in sys.path:
        sys.path.insert(0, path)


from argus_components import SemanticPostProcessBuilder
from common import load_json_config, log


def build_material_and_targets(config_path=None):
    """
    构建语义后处理材质和 RenderTarget。

    参数：
    - config_path:
        配置文件路径。
        如果为空，则由 load_json_config 使用默认配置路径。

    执行流程：
    1. 读取 pipeline 配置。
    2. 创建 SemanticPostProcessBuilder。
    3. 调用 builder.build_material_and_targets(cfg)。
    4. 输出材质和 RenderTarget 路径。
    """
    cfg, cfg_path = load_json_config(config_path)

    builder = SemanticPostProcessBuilder()
    result = builder.build_material_and_targets(cfg)

    log("配置文件: {}".format(cfg_path))
    log("Mask 编码方式: {}".format(result["mask_encoding"]))
    log("语义后处理材质已就绪: {}".format(result["material_path"]))
    log("RenderTarget 已就绪: {}".format(result["rt_rgb"]))
    log("RenderTarget 已就绪: {}".format(result["rt_mask"]))


if __name__ == "__main__":
    build_material_and_targets()
