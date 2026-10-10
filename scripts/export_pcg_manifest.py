"""Export the current realized PCG scene. This does not certify a new generation."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from argus_backends.ue.pcg import load_recipe, snapshot
from common import load_json_config, resolve_path


def export(output_path, config_path=str(ROOT / "config/pipeline_pcg.json")):
    cfg, _ = load_json_config(config_path)
    recipe, classes = load_recipe(cfg["pcg"]["recipe"], cfg["semantics"]["class_table_csv"])
    result = snapshot(recipe, classes)
    result["inspection_only"] = True
    path = Path(resolve_path(output_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
    return result
