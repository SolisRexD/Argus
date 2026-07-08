from pathlib import Path


UE_ENTRYPOINTS = [
    "scripts/batch_capture.py",
    "scripts/build_semantic_pp_material.py",
    "scripts/capture_rgb_and_mask.py",
    "scripts/export_scene_inventory.py",
    "scripts/setup_dual_capture.py",
    "scripts/validate_semantic_map.py",
    "scripts/writeback_semantic_stencil.py",
]


def test_ue_entrypoints_add_project_root_to_python_path():
    root = Path(__file__).resolve().parents[1]

    missing = []
    for rel_path in UE_ENTRYPOINTS:
        text = (root / rel_path).read_text(encoding="utf-8")
        if "PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)" not in text:
            missing.append(rel_path)
        if "for path in [PROJECT_ROOT, SCRIPT_DIR]" not in text:
            missing.append(rel_path)

    assert missing == []
