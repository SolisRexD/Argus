import copy
import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setitem(sys.modules, "unreal", types.SimpleNamespace())
    path = Path(__file__).resolve().parents[1] / "argus_backends/ue/pcg.py"
    spec = importlib.util.spec_from_file_location("isolated_pcg_backend", path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    backend.load_recipe = lambda *args: ({}, {})
    live = {"world": "PIE_world", "scene_sha256": "same", "saved_graph_sha256": "graph1", "generation_id": "current"}
    backend.snapshot = lambda *args: live
    cfg = {"pcg": {"recipe": "recipe.json"}, "_pcg_generation": copy.deepcopy(live),
           "semantics": {"class_table_csv": "classes.csv"},
           "runtime": {"auto_semantic_stencil": {"enabled": False}}}
    return backend, live, cfg


def test_capture_requires_generation_proof(guard):
    backend, _, cfg = guard
    del cfg["_pcg_generation"]
    with pytest.raises(RuntimeError, match="completed generation"):
        backend.assert_capture_ready(cfg)


@pytest.mark.parametrize("field,value", [("world", "editor_world"), ("scene_sha256", "moved_instance"),
                                        ("saved_graph_sha256", "edited_graph"), ("generation_id", "restarted_PIE")])
def test_capture_blocks_changed_world_scene_or_graph(guard, field, value):
    backend, live, cfg = guard
    live[field] = value
    with pytest.raises(RuntimeError, match="changed"):
        backend.assert_capture_ready(cfg)


def test_automatic_annotation_cannot_overwrite_generation_truth(guard):
    backend, _, cfg = guard
    cfg["runtime"]["auto_semantic_stencil"]["enabled"] = True
    with pytest.raises(RuntimeError, match="inference disabled"):
        backend.assert_capture_ready(cfg)


def test_valid_snapshot_passes(guard):
    backend, live, cfg = guard
    assert backend.assert_capture_ready(cfg) is live
