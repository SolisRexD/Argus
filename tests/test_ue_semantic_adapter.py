import os
from pathlib import Path
import subprocess
import sys


def test_ue_annotation_capabilities_can_be_inspected_without_unreal():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(root), env.get("PYTHONPATH", "")])
    code = """
import sys
from argus_backends.ue import default_annotation_capabilities

caps = default_annotation_capabilities()
assert caps.name == "ue"
assert caps.component_labeling is True
assert caps.material_slot_labeling is False
assert caps.instance_labeling is False
assert caps.proxy_labeling is True
assert "unreal" not in sys.modules
print("ue-capabilities-ok")
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ue-capabilities-ok"
