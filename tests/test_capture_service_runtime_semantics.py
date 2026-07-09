import ast
from pathlib import Path


def test_capture_service_passes_pose_to_runtime_semantic_controller():
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "argus_components" / "capture_system.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    apply_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "apply"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "semantic_stencil_controller"
    ]

    assert len(apply_calls) == 1

    pose_keywords = [
        keyword
        for keyword in apply_calls[0].keywords
        if keyword.arg == "pose"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "pose"
    ]

    assert pose_keywords
