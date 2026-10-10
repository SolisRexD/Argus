"""Generate or regenerate the open PCG lab; returns an asynchronous job."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from argus_backends.ue.pcg import load_recipe
from argus_components.pcg_generation import PCGGenerationJob
from common import load_json_config


def generate(seed=None, config_path=str(ROOT / "config/pipeline_pcg.json")):
    cfg, _ = load_json_config(config_path)
    recipe, classes = load_recipe(cfg["pcg"]["recipe"], cfg["semantics"]["class_table_csv"])
    return PCGGenerationJob(recipe, classes, recipe["seed"] if seed is None else seed).start()


if __name__ == "__main__":
    generate()
