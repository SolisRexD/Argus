"""Apply runtime settings that must be configured before PIE starts."""

import json
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

for path in (PROJECT_ROOT, SCRIPT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from argus_components import RuntimePlaySessionController
from common import load_json_config


def prepare(config_path=None):
    cfg, _ = load_json_config(config_path)
    plan = RuntimePlaySessionController().prepare_before_play(cfg)
    print("ARGUS_RUNTIME_PLAY_PREPARE=" + json.dumps(plan.to_metadata(), ensure_ascii=False))
    return plan


if __name__ == "__main__":
    prepare()
