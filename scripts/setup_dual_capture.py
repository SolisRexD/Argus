"""
创建或更新配置中定义的 SceneCapture Actor。

本脚本是 UE Python 直接运行的入口脚本之一。

它负责：
1. 读取 Argus 配置文件。
2. 根据 capture stream 配置创建或复用 SceneCapture2D Actor。
3. 配置每一路 SceneCapture 的 RenderTarget。
4. 配置 CaptureSource。
5. 给需要的 stream 设置后处理材质。
6. 同步主 stream 与从 stream 的位置、旋转和相机内参。
7. 保存当前关卡。

注意：
- 真正的 setup 逻辑在 DualCaptureSetupService 中。
- 本脚本只负责读取配置、调用 setup 服务、打印结果。
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

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


from argus_components import DualCaptureSetupService
from common import load_json_config, log


def setup(config_path=None):
    """
    初始化 SceneCapture Actor。

    参数：
    - config_path:
        配置文件路径。
        如果为空，则由 load_json_config 使用默认配置路径。

    返回：
    - result:
        包含 rgb_actor 和 mask_actor label 的字典。

    说明：
    - 为了兼容旧版调用方式，DualCaptureSetupService.setup(cfg)
      仍然返回 rgb_actor, mask_actor。
    - 即使内部已经支持多路 streams，这里也保留 RGB/MASK 日志。
    """
    cfg, cfg_path = load_json_config(config_path)

    service = DualCaptureSetupService()
    rgb_actor, mask_actor = service.setup(cfg)

    rgb_label = rgb_actor.get_actor_label() if rgb_actor else ""
    mask_label = mask_actor.get_actor_label() if mask_actor else ""

    log("配置文件: {}".format(cfg_path))
    log("SceneCapture setup 完成")
    log("RGB actor: {}".format(rgb_label))
    log("MASK actor: {}".format(mask_label))

    return {
        "rgb_actor": rgb_label,
        "mask_actor": mask_label,
    }


if __name__ == "__main__":
    setup()