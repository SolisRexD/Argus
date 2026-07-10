"""JSON configuration loading independent from engine runtimes."""

import json
import os

from .paths import get_project_root, resolve_path


def load_json_config(config_path=None, project_root=None):
    root = project_root or get_project_root()
    path = config_path or os.path.join(root, "config", "pipeline_config.json")
    abs_path = resolve_path(path, root)

    with open(abs_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    return data, abs_path
